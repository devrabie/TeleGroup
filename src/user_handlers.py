import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src import config
from src.database import get_all_plans

log = logging.getLogger(__name__)


# --- /subscribe command ---

@filters.command("subscribe")
async def subscribe_handler(client: Client, message: Message):
    """
    Handles the /subscribe command, showing available plans to the user.
    """
    log.info(f"User {message.from_user.id} requested /subscribe.")

    plans = get_all_plans(active_only=True)
    if not plans:
        await message.reply_text("There are currently no subscription plans available. Please check back later.")
        return

    buttons = []
    for plan in plans:
        # Text for the button, e.g., "Basic - 100 Stars"
        button_text = f"{plan['name']} - {plan['price_stars']} Stars"
        # Callback data to identify the plan, e.g., "select_plan_1"
        callback_data = f"select_plan_{plan['id']}"
        buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    if not buttons:
        await message.reply_text("No active plans to display.")
        return

    reply_markup = InlineKeyboardMarkup(buttons)
    await message.reply_text(
        "Please select a subscription plan from the list below:",
        reply_markup=reply_markup
    )


from pyrogram.types import CallbackQuery, LabeledPrice, PreCheckoutQuery
from src.database import (
    get_plan_by_id, grant_subscription, get_user_details, add_managed_account,
    delete_managed_account, toggle_account_status, reassign_proxy, get_account_stats
)
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordRequired
)

# In-memory storage for the login flow.
# In a real-world, scalable bot, this should be moved to a persistent store like Redis.
user_sessions = {}  # {user_id: {"client": PyrogramClient, "phone": str}}
user_states = {}    # {user_id: "state_name"}


# --- Callbacks and Payment ---

@filters.create(lambda _, __, query: query.data.startswith("select_plan_"))
async def select_plan_callback_handler(client: Client, callback_query: CallbackQuery):
    """Handles the user selecting a subscription plan from the inline keyboard."""
    plan_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id
    log.info(f"User {user_id} selected plan {plan_id}.")

    plan = get_plan_by_id(plan_id)
    if not plan:
        await callback_query.answer("This plan is no longer available.", show_alert=True)
        return

    # Prepare invoice
    title = f"Subscription: {plan['name']}"
    description = f"Access to {plan['max_accounts']} accounts and {plan['daily_group_limit']} groups/day."
    payload = f"plan_{plan_id}_user_{user_id}"
    price = LabeledPrice("Subscription", plan['price_stars'] * 100) # Price is in the smallest units of the currency

    try:
        await client.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token=config.PAYMENT_PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars currency code
            prices=[price],
            start_parameter="subscribe"
        )
        await callback_query.answer() # Acknowledge the button press
    except Exception as e:
        log.error(f"Failed to send invoice for plan {plan_id} to user {user_id}: {e}")
        await callback_query.answer("Could not process your request. Please try again.", show_alert=True)


@filters.pre_checkout_query
async def pre_checkout_handler(client: Client, pre_checkout_query: PreCheckoutQuery):
    """Confirms to Telegram that the bot is ready to accept the payment."""
    log.info(f"Received pre_checkout_query from user {pre_checkout_query.from_user.id}")
    await pre_checkout_query.answer(ok=True)


@filters.successful_payment
async def successful_payment_handler(client: Client, message: Message):
    """Handles a successful payment, activating the user's subscription."""
    user_id = message.from_user.id
    log.info(f"Received successful payment from user {user_id}")

    payment_info = message.successful_payment
    payload = payment_info.invoice_payload

    # Expected payload format: "plan_{plan_id}_user_{user_id}"
    try:
        plan_id = int(payload.split("_")[1])
    except (IndexError, ValueError):
        log.error(f"Invalid payload received from successful payment: {payload}")
        # Notify user of an issue
        await client.send_message(user_id, "There was an issue processing your subscription. Please contact support.")
        return

    # For now, we'll assume a 30-day subscription for any payment
    # In a real scenario, you might have different durations
    duration_days = 30

    success, msg = grant_subscription(user_id, plan_id, duration_days)

    if success:
        await client.send_message(user_id, f"✅ Thank you! Your subscription is now active for {duration_days} days.")
    else:
        log.error(f"Failed to grant subscription via payment for payload: {payload}. Reason: {msg}")
        await client.send_message(user_id, f"There was a database error activating your subscription. Please contact support with payload: `{payload}`")


# --- Account Adding Flow ---

@filters.command("add_account")
async def add_account_handler(client: Client, message: Message):
    """Starts the process of adding a new Telegram account."""
    user_id = message.from_user.id
    log.info(f"User {user_id} initiated /add_account.")

    details = get_user_details(user_id)
    if not details or not details.get('subscription'):
        await message.reply_text("You need an active subscription to add accounts. Use /subscribe to get one.")
        return

    sub = details['subscription']
    accounts = details['accounts']
    plan = get_plan_by_id(sub['plan_id'])

    if not plan:
        await message.reply_text("Your subscription plan could not be found. Please contact support.")
        return

    if len(accounts) >= plan['max_accounts']:
        await message.reply_text(f"You have reached the maximum of {plan['max_accounts']} accounts for your '{plan['name']}' plan.")
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
        "Please send the phone number of the account you want to add.\n"
        "<i>(Must be in international format, e.g., +1234567890)</i>"
    )

@filters.command("cancel")
async def cancel_handler(client: Client, message: Message):
    """Cancels the current operation (like adding an account)."""
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
        if user_id in user_sessions:
            if user_sessions[user_id]['client'].is_connected:
                await user_sessions[user_id]['client'].disconnect()
            del user_sessions[user_id]
        await message.reply_text("Operation cancelled.")
    else:
        await message.reply_text("Nothing to cancel.")


@filters.private & ~filters.command()
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
    phone_number = message.text

    await message.reply_text(f"Trying to log in with <code>{phone_number}</code>. Please wait...",)

    # Create a new client instance for the user in memory
    user_client = Client(
        f"user_session_{user_id}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True # Use in-memory storage for the session string
    )

    user_sessions[user_id] = {"client": user_client, "phone": phone_number}

    try:
        await user_client.connect()
        sent_code_info = await user_client.send_code(phone_number)
        user_sessions[user_id]['phone_code_hash'] = sent_code_info.phone_code_hash

        user_states[user_id] = "awaiting_code"
        await message.reply_text(
            "A login code has been sent to your Telegram account. Please send it here.\n"
            "Use /cancel to stop this process."
        )
    except PhoneNumberInvalid:
        await message.reply_text("The phone number is invalid. Please try again with a valid number in international format.")
        # State remains 'awaiting_phone'
    except Exception as e:
        log.error(f"Error during phone number handling for user {user_id}: {e}")
        await message.reply_text("An unexpected error occurred. Please try again or use /cancel.")
        del user_states[user_id]


async def handle_phone_code(client: Client, message: Message):
    user_id = message.from_user.id
    code = message.text.strip()
    session_info = user_sessions.get(user_id)

    if not session_info:
        await message.reply_text("Your session has expired. Please start over with /add_account.")
        del user_states[user_id]
        return

    user_client = session_info['client']
    try:
        await user_client.sign_in(
            session_info['phone'],
            session_info['phone_code_hash'],
            code
        )
        # If we are here, login was successful (or 2FA is needed)
        await complete_login(user_client, message)

    except SessionPasswordRequired:
        user_states[user_id] = "awaiting_password"
        await message.reply_text("This account has Two-Factor Authentication enabled. Please send your password.\nUse /cancel to stop.")
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await message.reply_text("Invalid or expired code. Please send the correct code again.")
        # State remains 'awaiting_code'
    except Exception as e:
        log.error(f"Error during code handling for user {user_id}: {e}")
        await message.reply_text("An unexpected error occurred. Please try again or use /cancel.")
        if user_id in user_states: del user_states[user_id]


async def handle_password(client: Client, message: Message):
    user_id = message.from_user.id
    password = message.text
    session_info = user_sessions.get(user_id)

    if not session_info:
        await message.reply_text("Your session has expired. Please start over with /add_account.")
        del user_states[user_id]
        return

    user_client = session_info['client']
    try:
        await user_client.check_password(password)
        await complete_login(user_client, message)
    except Exception as e:
        log.error(f"Error during password handling for user {user_id}: {e}")
        await message.reply_text("Incorrect password or an error occurred. Please try again or use /cancel.")
        # State remains 'awaiting_password'


async def complete_login(user_client: Client, message: Message):
    """Finalizes the login process, saves the session, and cleans up."""
    user_id = message.from_user.id
    session_info = user_sessions.get(user_id)
    phone = session_info['phone']

    session_string = await user_client.export_session_string()
    await user_client.disconnect()

    if add_managed_account(user_id, phone, session_string):
        await message.reply_text("✅ Account added successfully!")
    else:
        await message.reply_text("❌ Could not save your account to the database. It might already be registered.")

    # Cleanup
    if user_id in user_states: del user_states[user_id]
    if user_id in user_sessions: del user_sessions[user_id]


# --- User Dashboard ---

@filters.command("my_accounts")
async def my_accounts_handler(client: Client, message: Message):
    """Displays a list of the user's managed accounts with control buttons."""
    user_id = message.from_user.id
    details = get_user_details(user_id)

    if not details or not details['accounts']:
        await message.reply_text("You have not added any accounts yet. Use /add_account to get started.")
        return

    await message.reply_text("Your managed accounts:")
    for acc in details['accounts']:
        acc_id = acc['id']
        status = "🟢 Active" if acc['is_active'] else "🔴 Inactive"
        text = f"<b>Account:</b> <code>{acc['phone']}</code>\n<b>Status:</b> {status}"

        buttons = [
            [
                InlineKeyboardButton("📊 Stats", callback_data=f"mng_stats_{acc_id}"),
                InlineKeyboardButton("Toggle " + ("Off" if acc['is_active'] else "On"), callback_data=f"mng_toggle_{acc_id}")
            ],
            [
                InlineKeyboardButton("🔄 Change Proxy", callback_data=f"mng_proxy_{acc_id}"),
                InlineKeyboardButton("❌ Delete", callback_data=f"mng_delete_{acc_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_text(text, reply_markup=reply_markup)

# --- Dashboard Callbacks ---

@filters.create(lambda _, __, query: query.data.startswith("mng_"))
async def manage_account_callback_handler(client: Client, callback_query: CallbackQuery):
    """Main router for all management callbacks."""
    user_id = callback_query.from_user.id
    action, account_id_str = callback_query.data.split("_", 2)[1:]
    account_id = int(account_id_str)

    if action == "stats":
        total_groups = get_account_stats(account_id)
        await callback_query.answer(f"This account has created {total_groups} groups.", show_alert=True)

    elif action == "toggle":
        new_status = toggle_account_status(account_id, user_id)
        if new_status is not None:
            status_text = "activated" if new_status else "deactivated"
            await callback_query.answer(f"Account has been {status_text}.")
            # TODO: Refresh the original message to show the new status
        else:
            await callback_query.answer("Could not change status.", show_alert=True)
        # To refresh the message, one would typically edit the original message with the new state.
        # This requires more complex state management to refetch and rebuild the message.
        # For now, we just show an alert.

    elif action == "proxy":
        success, msg = reassign_proxy(account_id, user_id)
        await callback_query.answer(msg, show_alert=True)

    elif action == "delete":
        # Ask for confirmation
        buttons = [
            [
                InlineKeyboardButton("Yes, delete it", callback_data=f"mng_delete_confirm_{account_id}"),
                InlineKeyboardButton("No, cancel", callback_data="mng_cancel")
            ]
        ]
        await callback_query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
        await callback_query.answer()

    elif action == "delete_confirm":
        if delete_managed_account(account_id, user_id):
            await callback_query.message.edit_text("✅ Account has been deleted.")
        else:
            await callback_query.message.edit_text("❌ Could not delete account.")
        await callback_query.answer()

    elif action == "cancel":
        # This is a simple way to cancel the delete confirmation.
        # A better way would be to refetch and rebuild the original button layout.
        await callback_query.message.delete()
        await callback_query.answer("Cancelled.")


# --- Handler Registration ---
# A list of all handlers to be registered in the main app
user_handlers_list = [
    subscribe_handler,
    select_plan_callback_handler,
    pre_checkout_handler,
    successful_payment_handler,
    add_account_handler,
    cancel_handler,
    my_accounts_handler,
    manage_account_callback_handler, # Handles all `mng_*` callbacks
    conversation_handler, # Must be last to act as a fallback for non-command messages
]
