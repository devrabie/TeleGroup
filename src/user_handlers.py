import logging
import asyncio
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    PreCheckoutQueryHandler,
)

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired
)

from src import config
from src.database import (
    get_all_plans, get_plan_by_id, grant_subscription, get_user_details, add_managed_account,
    delete_managed_account, toggle_account_status, reassign_proxy, get_account_stats,
    set_user_language, get_random_proxy_id, get_proxy_string, get_account_session_string,
    get_or_create_user
)
from src.translation import get_translation_func_for_user
from pyrogram import Client
from pyrogram.enums import ChatType

log = logging.getLogger(__name__)

# --- Handlers for various bot features ---

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None):
    """Displays the main menu with inline buttons."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)

    keyboard = [
        [InlineKeyboardButton(_("🚀 Subscribe"), callback_data='main_subscribe'),
         InlineKeyboardButton(_("👤 My Accounts"), callback_data='main_my_accounts')],
        [InlineKeyboardButton(_("➕ Add Account"), callback_data='start_add_account'),
         InlineKeyboardButton(_("🌐 Language"), callback_data='main_language')],
        [InlineKeyboardButton(_("❓ Help"), callback_data='main_help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = _("Welcome to the main menu. Please choose an option:")

    if message_id:
        await context.bot.edit_message_text(chat_id=user_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greets the user, ensures they are in the DB, and shows the main menu."""
    user_id = update.effective_user.id
    get_or_create_user(user_id) # Ensure user is in the database
    await main_menu(update, context)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action = query.data.split('_')[1]

        if action == 'subscribe':
            await subscribe_handler(update, context)
        elif action == 'my_accounts':
            await my_accounts_handler(update, context)
        elif action == 'language':
            await language_handler(update, context)
        elif action == 'help':
            await help_handler(update, context)
        elif action == 'back':
            await main_menu(update, context, message_id=query.message.message_id)
    except Exception as e:
        log.error(f"Error in main_menu_callback: {e}", exc_info=True)
        try:
            _ = get_translation_func_for_user(query.from_user.id)
            await query.message.reply_text(_("An error occurred. Please try again later."))
        except Exception as inner_e:
            log.error(f"Failed to even notify user about the main_menu_callback error: {inner_e}")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides a detailed help message, showing admin commands to admins."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)

    # Base help text for all users
    help_text = _(
        "<b>Bot Help & Commands</b>\n\n"
        "Here is a list of commands you can use:\n\n"
        "<b>/start</b> - Shows the welcome message.\n"
        "<b>/help</b> - Shows this help message.\n"
        "<b>/subscribe</b> - Browse and purchase a subscription plan to use the bot's features.\n"
        "<b>/my_accounts</b> - View and manage your connected Telegram accounts.\n"
        "<b>/add_account</b> - Start the process of adding a new Telegram account for the bot to manage.\n"
        "<b>/language</b> - Change the display language of the bot (English/العربية).\n\n"
        "For most features, you need an active subscription. You can get one via the /subscribe command."
    )

    # Add admin commands if the user is an admin
    if user_id in config.ADMIN_IDS:
        admin_help_text = _(
            "\n\n"
            "<b>--- Admin Commands ---</b>\n"
            "<b>/create_plan</b> - Create a new subscription plan.\n"
            "<b>/list_plans</b> - List all plans.\n"
            "<b>/list_users</b> - List all bot users.\n"
            "<b>/view_user</b> - View details for a specific user.\n"
            "<b>/grant_subscription</b> - Manually grant a subscription."
        )
        help_text += admin_help_text

    buttons = [[InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')]]
    reply_markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query:
        await query.edit_message_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    plans = get_all_plans(active_only=True)

    text = _("Please select a subscription plan from the list below:")

    if not plans:
        text = _("There are currently no subscription plans available. Please check back later.")
        buttons = []
    else:
        buttons = [[InlineKeyboardButton(
            _("{plan_name} - {price} Stars").format(plan_name=p['name'], price=p['price_stars']),
            callback_data=f"select_plan_{p['id']}"
        )] for p in plans]

    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
    reply_markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query:
        # This handler is only ever called from a callback in the new flow, but we keep the check for robustness
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def select_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    plan_id = int(query.data.split("_")[2])
    plan = get_plan_by_id(plan_id)
    title = _("Subscription: {plan_name}").format(plan_name=plan['name'])
    description = _("Access to {accounts} accounts and {limit} groups/day.").format(
        accounts=plan['max_accounts'], limit=plan['daily_group_limit']
    )
    payload = f"plan_{plan_id}_user_{user_id}"
    price = LabeledPrice(_("Subscription"), plan['price_stars'])
    await context.bot.send_invoice(
        chat_id=user_id, title=title, description=description, payload=payload,
        provider_token="", currency="XTR", prices=[price]
    )

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    payload = update.message.successful_payment.invoice_payload
    plan_id = int(payload.split("_")[1])
    plan = get_plan_by_id(plan_id)
    duration_days = plan['duration_days']
    success, msg = grant_subscription(user_id, plan_id, duration_days)
    if success:
        reply_text = _("✅ Thank you! Your '{plan_name}' subscription is now active for {days} days.").format(
            plan_name=plan['name'], days=duration_days)
    else:
        reply_text = _("There was a database error activating your subscription. Please contact support with payload: `{payload}`").format(
            payload=payload)
    await update.message.reply_text(reply_text)

async def language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    buttons = [
        [InlineKeyboardButton("English 🇬🇧", callback_data="set_lang_en"),
         InlineKeyboardButton("العربية 🇸🇦", callback_data="set_lang_ar")],
        [InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    text = _("Please choose your language:")

    query = update.callback_query
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def set_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[2]
    user_id = query.from_user.id
    set_user_language(user_id, lang_code)
    _new = get_translation_func_for_user(user_id)
    await query.edit_message_text(
        _new("Language changed successfully."),
        parse_mode=ParseMode.HTML
    )

# --- Add Account Conversation ---
PHONE, CODE, PASSWORD = range(3)

async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    details = get_user_details(user_id)

    # Determine the message object and how to reply/edit
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
        # We will send a new message instead of editing, to make it clear we expect a reply.
        reply_func = context.bot.send_message
        reply_kwargs = {'chat_id': user_id}
    else:
        message = update.message
        reply_func = message.reply_text
        reply_kwargs = {}

    if not (details and details.get('subscription')):
        await reply_func(text=_("You need an active subscription to add accounts. Use /subscribe to get one."), **reply_kwargs)
        return ConversationHandler.END

    plan = get_plan_by_id(details['subscription']['plan_id'])
    if len(details['accounts']) >= plan['max_accounts']:
        await reply_func(text=_("You have reached the maximum of {max_accounts} accounts for your '{plan_name}' plan.").format(
            max_accounts=plan['max_accounts'], plan_name=plan['name']), **reply_kwargs)
        return ConversationHandler.END

    text = _("Please send the phone number of the account you want to add.\n<i>(Must be in international format, e.g., +1234567890)</i>")
    if query:
        # If started from a button, edit the message and add a cancel button
        keyboard = [[InlineKeyboardButton(_("❌ Cancel"), callback_data='cancel_conv')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await reply_func(
            text=text,
            parse_mode=ParseMode.HTML,
            **reply_kwargs
        )
    return PHONE

async def async_send_code(phone, context, user_id, _):
    proxy_id = get_random_proxy_id()
    proxy_string = get_proxy_string(proxy_id) if proxy_id else None
    proxy_dict = None

    if proxy_string:
        try:
            hostname, port, username, password = proxy_string.split(':')
            proxy_dict = {
                "scheme": "socks5",
                "hostname": hostname,
                "port": int(port),
                "username": username,
                "password": password,
            }
            log.info(f"Using proxy {hostname} for login attempt for user {user_id}")
        except (ValueError, IndexError) as e:
            log.error(f"Invalid proxy format during login: '{proxy_string}'. Error: {e}")
            # Continue without proxy if format is bad
    else:
        log.warning(f"No proxy available for login attempt for user {user_id}. Proceeding without proxy.")

    client = Client(
        f"user_session_{phone}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
        proxy=proxy_dict
    )
    context.user_data['pyrogram_client'] = client
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        await context.bot.send_message(user_id, _("A login code has been sent. Please send it here."))
    except PhoneNumberInvalid:
        await context.bot.send_message(user_id, _("The phone number is invalid. Please try again."))
    except Exception as e:
        log.error(f"Error sending code for user {user_id}: {e}")
        await context.bot.send_message(user_id, _("An unexpected error occurred."))

async def receive_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    phone_number = update.message.text
    context.user_data['phone'] = phone_number
    await update.message.reply_text(_("Processing... Please wait."))
    asyncio.create_task(async_send_code(phone_number, context, user_id, _))
    return CODE

async def async_sign_in(code, context, user_id, _):
    client = context.user_data['pyrogram_client']
    phone = context.user_data['phone']
    phone_code_hash = context.user_data['phone_code_hash']
    next_state = ConversationHandler.END
    try:
        await client.sign_in(phone, phone_code_hash, code)
        await async_complete_login(context, user_id, _)
    except SessionPasswordNeeded:
        await context.bot.send_message(user_id, _("This account has Two-Factor Authentication enabled. Please send your password."))
        next_state = PASSWORD
    except (PhoneCodeInvalid, PhoneCodeExpired):
        await context.bot.send_message(user_id, _("Invalid or expired code. Please send the correct code again."))
        next_state = CODE
    except Exception as e:
        log.error(f"Error signing in for user {user_id}: {e}")
        await context.bot.send_message(user_id, _("An unexpected error occurred."))
    context.user_data['next_state'] = next_state

async def receive_phone_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    code = update.message.text
    asyncio.create_task(async_sign_in(code, context, user_id, _))
    await update.message.reply_text(_("Processing..."))
    # The state transition is problematic here. We'll let the user send the password if needed.
    return PASSWORD

async def async_check_password(password, context, user_id, _):
    client = context.user_data['pyrogram_client']
    try:
        await client.check_password(password)
        await async_complete_login(context, user_id, _)
    except Exception as e:
        log.error(f"Error with 2FA for user {user_id}: {e}")
        await context.bot.send_message(user_id, _("Incorrect password or an error occurred. Please try again or use /cancel."))

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    password = update.message.text
    asyncio.create_task(async_check_password(password, context, user_id, _))
    return ConversationHandler.END

async def async_complete_login(context, user_id, _):
    client = context.user_data['pyrogram_client']
    phone = context.user_data['phone']
    session_string = await client.export_session_string()
    await client.disconnect()
    if add_managed_account(user_id, phone, session_string):
        await context.bot.send_message(user_id, _("✅ Account added successfully!"))
    else:
        await context.bot.send_message(user_id, _("❌ Could not save your account to the database. It might already be registered."))
    context.user_data.clear()

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generic conversation cancellation function."""
    _ = get_translation_func_for_user(update.effective_user.id)
    if 'pyrogram_client' in context.user_data:
        client = context.user_data['pyrogram_client']
        if client.is_connected:
            await client.disconnect()
    context.user_data.clear()

    query = update.callback_query
    if query:
        # If cancelled from a button, answer the callback and show the main menu
        await query.answer()
        await main_menu(update, context, message_id=query.message.message_id)
    else:
        # If cancelled from a /cancel command, just send a reply
        await update.message.reply_text(_("Operation cancelled."))

    return ConversationHandler.END

add_account_conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("add_account", add_account_start),
        CallbackQueryHandler(add_account_start, pattern="^start_add_account$")
    ],
    states={
        PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone_number)],
        CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone_code)],
        PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_conversation),
        CallbackQueryHandler(cancel_conversation, pattern="^cancel_conv$")
    ],
    conversation_timeout=300,
    per_message=False,
)

# --- User Dashboard ---

async def my_accounts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of the user's managed accounts to select from."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    details = get_user_details(user_id)

    text = _("Please select an account to manage:")
    buttons = []

    if not details or not details['accounts']:
        text = _("You have not added any accounts yet. Use /add_account to get started.")
    else:
        for acc in details['accounts']:
            status_icon = "🟢" if acc['is_active'] else "🔴"
            button_text = f"{status_icon} {acc['phone']}"
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"mng_select_{acc['id']}")])

    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
    reply_markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def account_detail_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int, message_id: int):
    """Displays the management menu for a single account."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)

    # We need to get the account details from the DB
    # This is a bit inefficient, a better way would be to get all accounts once
    # in my_accounts_handler and pass them around, but for now this is fine.
    details = get_user_details(user_id)
    acc = next((acc for acc in details['accounts'] if acc['id'] == account_id), None)

    if not acc:
        await context.bot.edit_message_text(chat_id=user_id, message_id=message_id, text=_("Error: Account not found."))
        return

    status = _("🟢 Active") if acc['is_active'] else _("🔴 Inactive")
    text = _("<b>Account:</b> <code>{phone}</code>\n<b>Status:</b> {status}").format(phone=acc['phone'], status=status)

    buttons = [
        [
            InlineKeyboardButton(_("📊 Stats"), callback_data=f"mng_stats_{acc['id']}"),
            InlineKeyboardButton(_("Toggle On") if not acc['is_active'] else _("Toggle Off"), callback_data=f"mng_toggle_{acc['id']}"),
        ],
        [
            InlineKeyboardButton(_("🔄 Change Proxy"), callback_data=f"mng_proxy_{acc['id']}"),
            InlineKeyboardButton(_("❌ Delete"), callback_data=f"mng_delete_{acc['id']}"),
        ],
        [InlineKeyboardButton(_("📂 View Groups"), callback_data=f"mng_viewgroups_{acc['id']}")],
        [InlineKeyboardButton(_("🔙 Back to Account List"), callback_data="mng_back_list")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await context.bot.edit_message_text(chat_id=user_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def manage_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for all management callbacks."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    log.info(f"User {user_id} triggered manage_account_callback with data: {query.data}")

    try:
        _ = get_translation_func_for_user(user_id)
        action_parts = query.data.split("_")
        action = action_parts[1]

        if action == "cancel":
            log.info(f"User {user_id} cancelled management action.")
            await query.message.delete()
            await context.bot.answer_callback_query(query.id, _("Cancelled."))
            return

        if action == "back":
            await my_accounts_handler(update, context, from_callback=True)
            return

        if action == "select":
            account_id = int(action_parts[2])
            await account_detail_menu(update, context, account_id, query.message.message_id)
            return

        if action == "stats":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} requested stats for account {account_id}.")
            total_groups = get_account_stats(account_id)

            # Re-get account details to display them again
            details = get_user_details(user_id)
            acc = next((acc for acc in details['accounts'] if acc['id'] == account_id), None)
            status = _("🟢 Active") if acc['is_active'] else _("🔴 Inactive")
            text = _("<b>Account:</b> <code>{phone}</code>\n<b>Status:</b> {status}\n\n📊 <b>Stats:</b> {count} groups created.").format(
                phone=acc['phone'], status=status, count=total_groups)

            # Re-create the same buttons
            buttons = [
                [
                    InlineKeyboardButton(_("📊 Stats"), callback_data=f"mng_stats_{acc['id']}"),
                    InlineKeyboardButton(_("Toggle On") if not acc['is_active'] else _("Toggle Off"), callback_data=f"mng_toggle_{acc['id']}"),
                ],
                [
                    InlineKeyboardButton(_("🔄 Change Proxy"), callback_data=f"mng_proxy_{acc['id']}"),
                    InlineKeyboardButton(_("❌ Delete"), callback_data=f"mng_delete_{acc['id']}"),
                ],
                [InlineKeyboardButton(_("🔙 Back to Account List"), callback_data="mng_back_list")]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

        elif action == "viewgroups":
            account_id = int(action_parts[2])
            page = int(action_parts[3]) if len(action_parts) > 3 else 0
            log.info(f"User {user_id} requested to view groups for account {account_id} on page {page}.")

            await query.edit_message_text(_("Fetching groups... Please wait."))

            session_string = get_account_session_string(account_id)
            if not session_string:
                await query.edit_message_text(_("Error: Could not retrieve session for this account."))
                return

            client = Client(f"user_session_reader_{account_id}", session_string=session_string, in_memory=True, api_id=config.API_ID, api_hash=config.API_HASH)

            try:
                await client.connect()

                all_groups = []
                async for dialog in client.get_dialogs():
                    if dialog.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                        all_groups.append(dialog.chat.title)

                await client.disconnect()

                if not all_groups:
                    text = _("This account is not a member of any groups.")
                    buttons = [[InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")]]
                    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
                    return

                # Pagination
                items_per_page = 10
                start_index = page * items_per_page
                end_index = start_index + items_per_page

                paginated_groups = all_groups[start_index:end_index]

                text = _("<b>Groups for Account (Page {page_num}/{total_pages}):</b>\n\n").format(
                    page_num=page + 1,
                    total_pages=(len(all_groups) + items_per_page - 1) // items_per_page
                )
                text += "\n".join([f"• <code>{group_name}</code>" for group_name in paginated_groups])

                pagination_buttons = []
                if page > 0:
                    pagination_buttons.append(InlineKeyboardButton(_("⬅️ Previous"), callback_data=f"mng_viewgroups_{account_id}_{page-1}"))
                if end_index < len(all_groups):
                    pagination_buttons.append(InlineKeyboardButton(_("Next ➡️"), callback_data=f"mng_viewgroups_{account_id}_{page+1}"))

                buttons = [pagination_buttons] if pagination_buttons else []
                buttons.append([InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")])

                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)

            except Exception as e:
                log.error(f"Error fetching groups for user {user_id}, account {account_id}: {e}")
                await query.edit_message_text(_("An error occurred while fetching groups. The session might be invalid or revoked."))
                if client.is_connected:
                    await client.disconnect()
        elif action == "groupstats":
            group_log_id = int(action_parts[2])
            log.info(f"User {user_id} requested stats for group log ID {group_log_id}.")
            details = get_group_log_details(group_log_id)
            if details:
                text = _("<b>Group Stats:</b>\n\n<b>Name:</b> {name}\n<b>Created:</b> {date}").format(
                    name=details['group_name'],
                    date=details['creation_timestamp']
                )
                await context.bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(user_id, _("Could not retrieve group stats."))
        elif action == "toggle":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} toggled account {account_id}.")
            new_status = toggle_account_status(account_id, user_id)
            if new_status is not None:
                status_text = _("activated") if new_status else _("deactivated")
                await context.bot.answer_callback_query(query.id, _("Account has been {status}.").format(status=status_text))
                # Refresh the menu
                await account_detail_menu(update, context, account_id, query.message.message_id)
            else:
                await context.bot.answer_callback_query(query.id, _("Could not change status."), show_alert=True)
        elif action == "proxy":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} reassigned proxy for account {account_id}.")
            success, msg = reassign_proxy(account_id, user_id)
            await context.bot.answer_callback_query(query.id, msg, show_alert=True)
            # Refresh the menu
            await account_detail_menu(update, context, account_id, query.message.message_id)
        elif action == "delete":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} initiated delete for account {account_id}.")
            buttons = [
                [InlineKeyboardButton(_("Yes, delete it"), callback_data=f"mng_deleteconfirm_{account_id}")],
                [InlineKeyboardButton(_("No, cancel"), callback_data=f"mng_select_{account_id}")]
            ]
            await query.edit_message_text(
                _("Are you sure you want to delete this account? This action cannot be undone."),
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        elif action == "deleteconfirm":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} confirmed delete for account {account_id}.")
            if delete_managed_account(account_id, user_id):
                await context.bot.answer_callback_query(query.id, _("✅ Account has been deleted."))
                # This is a bit of code duplication, but it's safer than calling the handler
                # and avoids state-related issues with the update object.
                details = get_user_details(user_id)
                text = _("Please select an account to manage:")
                buttons = []
                if not details or not details['accounts']:
                    text = _("You have not added any accounts yet. Use /add_account to get started.")
                else:
                    for acc in details['accounts']:
                        status_icon = "🟢" if acc['is_active'] else "🔴"
                        button_text = f"{status_icon} {acc['phone']}"
                        buttons.append([InlineKeyboardButton(button_text, callback_data=f"mng_select_{acc['id']}")])
                buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
                reply_markup = InlineKeyboardMarkup(buttons)
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(_("❌ Could not delete account."))
    except Exception as e:
        log.error(f"Error in manage_account_callback for user {user_id} with data {query.data}: {e}", exc_info=True)
        try:
            # Try to inform the user that something went wrong
            await context.bot.answer_callback_query(query.id, "An unexpected error occurred.", show_alert=True)
        except Exception as inner_e:
            log.error(f"Failed to even notify user about the error: {inner_e}")

# --- Handler Registration ---
user_handlers_list = [
    CommandHandler("start", start_handler),
    CommandHandler("help", help_handler),
    CommandHandler("subscribe", subscribe_handler),
    CallbackQueryHandler(select_plan_callback, pattern="^select_plan_"),
    PreCheckoutQueryHandler(precheckout_callback),
    MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback),
    CommandHandler("language", language_handler),
    CallbackQueryHandler(set_language_callback, pattern="^set_lang_"),
    CommandHandler("my_accounts", my_accounts_handler),
    CallbackQueryHandler(manage_account_callback, pattern="^mng_"),
    CallbackQueryHandler(main_menu_callback, pattern="^main_"),
    add_account_conv_handler,
]
