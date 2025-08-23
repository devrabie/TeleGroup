import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src import config
from src.database import get_all_plans

log = logging.getLogger(__name__)


# --- /subscribe command ---

async def subscribe_handler(client: Client, message: Message):
    """
    Handles the /subscribe command, showing available plans to the user.
    """
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    log.info(f"User {user_id} requested /subscribe.")

    plans = get_all_plans(active_only=True)
    if not plans:
        await message.reply_text(_("There are currently no subscription plans available. Please check back later."))
        return

    buttons = []
    for plan in plans:
        button_text = _("{plan_name} - {price} Stars").format(plan_name=plan['name'], price=plan['price_stars'])
        callback_data = f"select_plan_{plan['id']}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    if not buttons:
        await message.reply_text(_("No active plans to display."))
        return

    reply_markup = InlineKeyboardMarkup(buttons)
    await message.reply_text(
        _("Please select a subscription plan from the list below:"),
        reply_markup=reply_markup
    )


# In-memory storage for the login flow.
# In a real-world, scalable bot, this should be moved to a persistent store like Redis.
user_sessions = {}  # {user_id: {"client": PyrogramClient, "phone": str}}
user_states = {}    # {user_id: "state_name"}


# --- Callbacks and Payment ---

async def select_plan_callback_handler(client: Client, callback_query: CallbackQuery):
    """Handles the user selecting a subscription plan from the inline keyboard."""
    user_id = callback_query.from_user.id
    _ = get_translation_func_for_user(user_id)
    plan_id = int(callback_query.data.split("_")[2])
    log.info(f"User {user_id} selected plan {plan_id}.")

    plan = get_plan_by_id(plan_id)
    if not plan:
        await callback_query.answer(_("This plan is no longer available."), show_alert=True)
        return

    # Prepare invoice
    title = _("Subscription: {plan_name}").format(plan_name=plan['name'])
    description = _("Access to {accounts} accounts and {limit} groups/day.").format(
        accounts=plan['max_accounts'], limit=plan['daily_group_limit']
    )
    payload = f"plan_{plan_id}_user_{user_id}"
    price = LabeledPrice(_("Subscription"), plan['price_stars'] * 100)

    try:
        await client.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[price],
            start_parameter="subscribe"
        )
        await callback_query.answer()
    except Exception as e:
        log.error(f"Failed to send invoice for plan {plan_id} to user {user_id}: {e}")
        await callback_query.answer(_("Could not process your request. Please try again."), show_alert=True)


async def pre_checkout_handler(client: Client, pre_checkout_query: PreCheckoutQuery):
    """Confirms to Telegram that the bot is ready to accept the payment."""
    log.info(f"Received pre_checkout_query from user {pre_checkout_query.from_user.id}")
    await pre_checkout_query.answer(ok=True)


async def successful_payment_handler(client: Client, message: Message):
    """Handles a successful payment, activating the user's subscription."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    log.info(f"Received successful payment from user {user_id}")

    payment_info = message.successful_payment
    payload = payment_info.invoice_payload

    try:
        plan_id = int(payload.split("_")[1])
    except (IndexError, ValueError):
        log.error(f"Invalid payload received from successful payment: {payload}")
        await client.send_message(user_id, _("There was an issue processing your subscription. Please contact support."))
        return

    plan = get_plan_by_id(plan_id)
    if not plan:
        log.error(f"Could not find plan {plan_id} after successful payment. Payload: {payload}")
        await client.send_message(user_id, _("There was an issue finding your selected plan. Please contact support."))
        return

    duration_days = plan['duration_days']
    success, msg = grant_subscription(user_id, plan_id, duration_days)

    if success:
        reply_text = _("✅ Thank you! Your '{plan_name}' subscription is now active for {days} days.").format(
            plan_name=plan['name'], days=duration_days
        )
        await client.send_message(user_id, reply_text)
    else:
        log.error(f"Failed to grant subscription via payment for payload: {payload}. Reason: {msg}")
        reply_text = _("There was a database error activating your subscription. Please contact support with payload: `{payload}`").format(
            payload=payload
        )
        await client.send_message(user_id, reply_text)


# --- Account Adding Flow ---

async def add_account_handler(client: Client, message: Message):
    """Starts the process of adding a new Telegram account."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    log.info(f"User {user_id} initiated /add_account.")

    details = get_user_details(user_id)
    if not details or not details.get('subscription'):
        await message.reply_text(_("You need an active subscription to add accounts. Use /subscribe to get one."))
        return

    sub = details['subscription']
    accounts = details['accounts']
    plan = get_plan_by_id(sub['plan_id'])

    if not plan:
        await message.reply_text(_("Your subscription plan could not be found. Please contact support."))
        return

    if len(accounts) >= plan['max_accounts']:
        reply = _("You have reached the maximum of {max_accounts} accounts for your '{plan_name}' plan.").format(
            max_accounts=plan['max_accounts'], plan_name=plan['name']
        )
        await message.reply_text(reply)
        return

    # Cancel any previous attempts
    if user_id in user_states:
        del user_states[user_id]
    if user_id in user_sessions:
        if user_sessions[user_id]['client'].is_connected:
            await user_sessions[user_id]['client'].disconnect()
        del user_sessions[user_id]

    user_states[user_id] = "awaiting_phone"
    await message.reply_text(
        _("Please send the phone number of the account you want to add.\n"
          "<i>(Must be in international format, e.g., +1234567890)</i>")
    )

async def cancel_handler(client: Client, message: Message):
    """Cancels the current operation (like adding an account)."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    if user_id in user_states:
        del user_states[user_id]
        if user_id in user_sessions:
            if user_sessions[user_id]['client'].is_connected:
                await user_sessions[user_id]['client'].disconnect()
            del user_sessions[user_id]
        await message.reply_text(_("Operation cancelled."))
    else:
        await message.reply_text(_("Nothing to cancel."))


async def conversation_handler(client: Client, message: Message):
    """
    Handles the conversational steps for adding an account.
    This is a simple Finite State Machine (FSM).
    """
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state:
        return  # Not in a conversation, do nothing

    if state == "awaiting_phone":
        await handle_phone_number(client, message)
    elif state == "awaiting_code":
        await handle_phone_code(client, message)
    elif state == "awaiting_password":
        await handle_password(client, message)


async def handle_phone_number(client: Client, message: Message):
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    phone_number = message.text

    await message.reply_text(_("Trying to log in with <code>{phone_number}</code>. Please wait...").format(phone_number=phone_number))

    user_client = Client(
        f"user_session_{user_id}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True
    )
    user_sessions[user_id] = {"client": user_client, "phone": phone_number}

    try:
        await user_client.connect()
        sent_code_info = await user_client.send_code(phone_number)
        user_sessions[user_id]['phone_code_hash'] = sent_code_info.phone_code_hash

        user_states[user_id] = "awaiting_code"
        await message.reply_text(
            _("A login code has been sent to your Telegram account. Please send it here.\n"
              "Use /cancel to stop this process.")
        )
    except PhoneNumberInvalid:
        await message.reply_text(_("The phone number is invalid. Please try again with a valid number in international format."))
    except Exception as e:
        log.error(f"Error during phone number handling for user {user_id}: {e}")
        await message.reply_text(_("An unexpected error occurred. Please try again or use /cancel."))
        del user_states[user_id]


async def handle_phone_code(client: Client, message: Message):
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    code = message.text.strip()
    session_info = user_sessions.get(user_id)

    if not session_info:
        await message.reply_text(_("Your session has expired. Please start over with /add_account."))
        del user_states[user_id]
        return

    user_client = session_info['client']
    try:
        await user_client.sign_in(
            session_info['phone'],
            session_info['phone_code_hash'],
            code
        )
        await complete_login(user_client, message)

    except SessionPasswordRequired:
        user_states[user_id] = "awaiting_password"
        await message.reply_text(_("This account has Two-Factor Authentication enabled. Please send your password.\nUse /cancel to stop."))
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await message.reply_text(_("Invalid or expired code. Please send the correct code again."))
    except Exception as e:
        log.error(f"Error during code handling for user {user_id}: {e}")
        await message.reply_text(_("An unexpected error occurred. Please try again or use /cancel."))
        if user_id in user_states: del user_states[user_id]


async def handle_password(client: Client, message: Message):
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    password = message.text
    session_info = user_sessions.get(user_id)

    if not session_info:
        await message.reply_text(_("Your session has expired. Please start over with /add_account."))
        del user_states[user_id]
        return

    user_client = session_info['client']
    try:
        await user_client.check_password(password)
        await complete_login(user_client, message)
    except Exception as e:
        log.error(f"Error during password handling for user {user_id}: {e}")
        await message.reply_text(_("Incorrect password or an error occurred. Please try again or use /cancel."))


async def complete_login(user_client: Client, message: Message):
    """Finalizes the login process, saves the session, and cleans up."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    session_info = user_sessions.get(user_id)
    phone = session_info['phone']

    session_string = await user_client.export_session_string()
    await user_client.disconnect()

    if add_managed_account(user_id, phone, session_string):
        await message.reply_text(_("✅ Account added successfully!"))
    else:
        await message.reply_text(_("❌ Could not save your account to the database. It might already be registered."))

    if user_id in user_states: del user_states[user_id]
    if user_id in user_sessions: del user_sessions[user_id]


# --- User Dashboard ---

async def my_accounts_handler(client: Client, message: Message):
    """Displays a list of the user's managed accounts with control buttons."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    details = get_user_details(user_id)

    if not details or not details['accounts']:
        await message.reply_text(_("You have not added any accounts yet. Use /add_account to get started."))
        return

    await message.reply_text(_("Your managed accounts:"))
    for acc in details['accounts']:
        acc_id = acc['id']
        status = _("🟢 Active") if acc['is_active'] else _("🔴 Inactive")
        text = _("<b>Account:</b> <code>{phone}</code>\n<b>Status:</b> {status}").format(phone=acc['phone'], status=status)

        buttons = [
            [
                InlineKeyboardButton(_("📊 Stats"), callback_data=f"mng_stats_{acc_id}"),
                InlineKeyboardButton(_("Toggle On") if not acc['is_active'] else _("Toggle Off"), callback_data=f"mng_toggle_{acc_id}")
            ],
            [
                InlineKeyboardButton(_("🔄 Change Proxy"), callback_data=f"mng_proxy_{acc_id}"),
                InlineKeyboardButton(_("❌ Delete"), callback_data=f"mng_delete_{acc_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_text(text, reply_markup=reply_markup)

# --- Dashboard Callbacks ---

async def manage_account_callback_handler(client: Client, callback_query: CallbackQuery):
    """Main router for all management callbacks."""
    user_id = callback_query.from_user.id
    _ = get_translation_func_for_user(user_id)

    action_parts = callback_query.data.split("_")
    action = action_parts[1]
    account_id = int(action_parts[2]) if len(action_parts) > 2 else 0

    if action == "stats":
        total_groups = get_account_stats(account_id)
        await callback_query.answer(
            _("This account has created {count} groups.").format(count=total_groups),
            show_alert=True
        )

    elif action == "toggle":
        new_status = toggle_account_status(account_id, user_id)
        if new_status is not None:
            status_text = _("activated") if new_status else _("deactivated")
            await callback_query.answer(_("Account has been {status}.").format(status=status_text))
        else:
            await callback_query.answer(_("Could not change status."), show_alert=True)

    elif action == "proxy":
        success, msg = reassign_proxy(account_id, user_id)
        # This msg is not translated as it's from the DB and simple.
        # For a full implementation, we would use error codes.
        await callback_query.answer(msg, show_alert=True)

    elif action == "delete":
        buttons = [
            [
                InlineKeyboardButton(_("Yes, delete it"), callback_data=f"mng_deleteconfirm_{account_id}"),
                InlineKeyboardButton(_("No, cancel"), callback_data="mng_cancel")
            ]
        ]
        await callback_query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer()

    elif action == "deleteconfirm":
        if delete_managed_account(account_id, user_id):
            await callback_query.message.edit_text(_("✅ Account has been deleted."))
        else:
            await callback_query.message.edit_text(_("❌ Could not delete account."))
        await callback_query.answer()

    elif action == "cancel":
        await callback_query.message.delete()
        await callback_query.answer(_("Cancelled."))


# --- Language Selection ---

async def language_handler(client: Client, message: Message):
    """Allows the user to select their interface language."""
    user_id = message.from_user.id
    _ = get_translation_func_for_user(user_id)
    buttons = [
        [InlineKeyboardButton("English 🇬🇧", callback_data="set_lang_en")],
        [InlineKeyboardButton("العربية 🇸🇦", callback_data="set_lang_ar")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await message.reply_text(_("Please choose your language:"), reply_markup=reply_markup)


async def set_language_callback_handler(client: Client, callback_query: CallbackQuery):
    """Handles language selection callback."""
    lang_code = callback_query.data.split("_")[2]
    user_id = callback_query.from_user.id
    _ = get_translation_func_for_user(user_id) # Get translator for the old language

    if set_user_language(user_id, lang_code):
        _new = get_translation_func_for_user(user_id) # Get translator for the new language
        await callback_query.answer(_new("Language changed successfully."), show_alert=True)
    else:
        await callback_query.answer(_("Could not change language."), show_alert=True)

    await callback_query.message.delete()


# --- Handler Registration ---
# A list of tuples: (handler_function, filter, handler_type)
user_handlers_list = [
    (subscribe_handler, filters.command("subscribe") & filters.private, "message"),
    (select_plan_callback_handler, filters.create(lambda _, __, q: q.data.startswith("select_plan_")), "callback"),
    (pre_checkout_handler, filters.pre_checkout_query, "pre_checkout"),
    (successful_payment_handler, filters.successful_payment, "message"),
    (add_account_handler, filters.command("add_account") & filters.private, "message"),
    (cancel_handler, filters.command("cancel") & filters.private, "message"),
    (my_accounts_handler, filters.command("my_accounts") & filters.private, "message"),
    (language_handler, filters.command("language") & filters.private, "message"),
    (set_language_callback_handler, filters.create(lambda _, __, q: q.data.startswith("set_lang_")), "callback"),
    (manage_account_callback_handler, filters.create(lambda _, __, q: q.data.startswith("mng_")), "callback"),
    (conversation_handler, filters.private & ~filters.command, "message"),
]
