import logging
import asyncio
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    PreCheckoutQueryHandler,
)

import pyrogram
from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded,
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    Timeout,
    Forbidden
)

from src import config
from src.database import (
    get_all_plans, get_plan_by_id, grant_subscription, get_user_details, add_managed_account,
    delete_managed_account, toggle_account_status, toggle_code_monitor, reassign_proxy, get_account_stats,
    set_user_language, get_random_proxy_id, get_proxy_string, get_account_session_string,
    update_user_details, mark_proxy_as_bad, get_account_details, get_info_page_content,
    get_user_language, get_random_device_profile, get_device_profile_by_account_id,
    session_is_invalid, user_owns_account,
)
from src.translation import get_translation_func_for_user
from src.code_monitor import build_proxy_dict, code_monitor_manager, is_socks_auth_error
from src.account_explorer import display_name, format_session_health_text, get_cached_identity
from src.account_views import (
    load_identity_for_menu,
    show_conversation,
    show_inbox,
    show_profile,
)
from src.two_step import (
    TwoStepError,
    apply_two_step_password,
    get_two_step_status,
    validate_two_step_password,
)
from pyrogram.enums import ChatType, ChatMemberStatus

log = logging.getLogger(__name__)

def _format_datetime(dt_string: str | None) -> str:
    """Safely formats a UTC datetime string into the user's local timezone."""
    if not dt_string:
        return "N/A"

    try:
        display_tz = ZoneInfo(config.DISPLAY_TIMEZONE)
    except (ZoneInfoNotFoundError, AttributeError):
        display_tz = timezone.utc

    try:
        utc_time = datetime.fromisoformat(dt_string)
        if utc_time.tzinfo is None:
            utc_time = utc_time.replace(tzinfo=timezone.utc)

        local_time = utc_time.astimezone(display_tz)
        return local_time.strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return "Invalid Date"


def _is_message_not_modified(error: Exception) -> bool:
    """Telegram rejects edits that do not change the message text or markup."""
    return isinstance(error, BadRequest) and "not modified" in str(error).lower()


async def _safe_edit_message_text(bot, *, chat_id, message_id, text, reply_markup=None, parse_mode=None):
    """Edit a message, ignoring Telegram's 'message is not modified' error."""
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return True
    except BadRequest as e:
        if _is_message_not_modified(e):
            log.debug(f"Ignored unchanged message edit for chat {chat_id} message {message_id}.")
            return False
        raise

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
        [InlineKeyboardButton(_("ℹ️ Information & Policies"), callback_data='main_info_policies')],
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
    # This will create the user if they don't exist, and update their name/username if they do.
    update_user_details(update.effective_user)
    await main_menu(update, context)

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action = '_'.join(query.data.split('_')[1:])

        if action == 'subscribe':
            await subscribe_handler(update, context)
        elif action == 'my_accounts':
            await my_accounts_handler(update, context)
        elif action == 'language':
            await language_handler(update, context)
        elif action == 'info_policies':
            await show_info_policies_menu(update, context)
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
        "After adding an account, group creation is <b>off</b> by default. Open /my_accounts to enable group creation, login-code monitoring, or two-step verification for each account separately.\n\n"
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


async def show_info_policies_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the info & policies sub-menu."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)

    text = _("Please select a topic to read about:")
    keyboard = [
        [
            InlineKeyboardButton(_("📜 Privacy Policy"), callback_data='info_privacy'),
            InlineKeyboardButton(_("⚖️ Disclaimer"), callback_data='info_disclaimer')
        ],
        [
            InlineKeyboardButton(_("💳 Payment & Refunds"), callback_data='info_payment')
        ],
        [
            InlineKeyboardButton(_("ℹ️ About the Project"), callback_data='info_project')
        ],
        [
            InlineKeyboardButton(_("✨ Bot Features"), callback_data='info_features')
        ],
        [
            InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def info_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles callbacks for the info pages, displaying the content."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)

    page_key = query.data.split('_')[1]
    lang_code = get_user_language(user_id)

    # Map page_key to a title
    page_titles = {
        'privacy': _("📜 Privacy Policy"),
        'disclaimer': _("⚖️ Disclaimer"),
        'payment': _("💳 Payment & Refunds"),
        'project': _("ℹ️ About the Project"),
        'features': _("✨ Bot Features")
    }
    title = page_titles.get(page_key, _("Information"))

    content = get_info_page_content(page_key, lang_code)
    if not content:
        content = _("Content for this page is not available yet. Please check back later.")

    text = f"<b>{title}</b>\n\n{content}"

    keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='main_info_policies')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    plans = get_all_plans(active_only=True)

    text = _("Please select a subscription plan from the list below:")

    if not plans:
        text = _("There are currently no subscription plans available. Please check back later.")
        buttons = []
    else:
        buttons = []
        for p in plans:
            # Show both prices if USD price is set
            if p.get('price_usd') and p['price_usd'] > 0:
                button_text = _("{plan_name} - {price} Stars / ${price_usd:.2f}").format(
                    plan_name=p['name'], price=p['price_stars'], price_usd=p['price_usd']
                )
            else:
                button_text = _("{plan_name} - {price} Stars").format(
                    plan_name=p['name'], price=p['price_stars']
                )
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"select_plan_{p['id']}")])

    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
    reply_markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query:
        # This handler is only ever called from a callback in the new flow, but we keep the check for robustness
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

from src.cryptopay import cryptopay_client

async def select_plan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows payment method options after a plan is selected."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    plan_id = int(query.data.split("_")[2])
    plan = get_plan_by_id(plan_id)

    if not plan:
        await query.edit_message_text(_("This plan is no longer available. Please choose another one."))
        return

    text = _("You have selected the '<b>{plan_name}</b>' plan.\n\nPlease choose your payment method:").format(plan_name=plan['name'])
    buttons = [
        [InlineKeyboardButton(_("Pay with Stars ✨"), callback_data=f"pay_stars_{plan_id}")]
    ]
    # Only show the crypto button if the price is set
    if plan.get('price_usd') and plan['price_usd'] > 0:
        buttons.append([InlineKeyboardButton(_("Pay with Crypto 💳"), callback_data=f"pay_crypto_{plan_id}")])

    buttons.append([InlineKeyboardButton(_("🔙 Back to Plans"), callback_data='main_subscribe')])

    reply_markup = InlineKeyboardMarkup(buttons)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def pay_with_stars_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Pay with Stars' button, creating a Telegram Stars invoice."""
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

    # Note: provider_token for Stars is an empty string
    await context.bot.send_invoice(
        chat_id=user_id, title=title, description=description, payload=payload,
        provider_token="", currency="XTR", prices=[price]
    )


async def pay_with_crypto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Pay with Crypto' button, creating a Crypto Pay invoice."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)
    plan_id = int(query.data.split("_")[2])
    plan = get_plan_by_id(plan_id)

    if not cryptopay_client:
        await query.edit_message_text(_("Crypto payments are not configured by the admin yet."))
        return

    # Create a unique payload for the invoice so we can identify the user and plan later
    invoice_payload = f"plan_{plan_id}_user_{user_id}"

    await query.edit_message_text(_("Creating your crypto invoice, please wait..."))

    # The documentation specifies these parameters
    invoice = await cryptopay_client.create_invoice(
        amount=plan['price_usd'],
        payload=invoice_payload,
        paid_btn_name="callback",
        paid_btn_url=f"https://t.me/{context.bot.username}?start=start"
    )

    if invoice and invoice.get('bot_invoice_url'):
        text = _("Your invoice has been created. Please use the button below to pay.")
        buttons = [
            [InlineKeyboardButton(_("Pay Invoice"), url=invoice['bot_invoice_url'])],
            [InlineKeyboardButton(_("🔙 Back"), callback_data=f"select_plan_{plan_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        log.error(f"Failed to create Crypto Pay invoice for user {user_id}, plan {plan_id}. Response: {invoice}")
        text = _("Sorry, we could not create a crypto invoice at this moment. Please try again later or contact support.")
        buttons = [[InlineKeyboardButton(_("🔙 Back"), callback_data=f"select_plan_{plan_id}")]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(text, reply_markup=reply_markup)

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

MAX_PROXY_RETRIES = 3

async def async_send_code(phone, context, user_id, _):
    """
    Tries to connect to Telegram and send a login code.
    Retries with a new proxy and device profile if the connection fails or if a CAPTCHA is requested.
    """
    for attempt in range(MAX_PROXY_RETRIES):
        # --- Get a new proxy and device profile for each attempt ---
        proxy_id = get_random_proxy_id()
        proxy_string = get_proxy_string(proxy_id) if proxy_id else None
        proxy_dict = None
        client = None

        device_profile = get_random_device_profile()
        if not device_profile:
            log.error(f"Could not get a device profile for user {user_id} on attempt {attempt + 1}. Aborting login.")
            await context.bot.send_message(user_id, _("Could not prepare a secure session. Please contact support."))
            return

        # Store the chosen profile ID to be saved with the account later
        context.user_data['device_profile_id'] = device_profile['id']

        # --- Prepare Proxy ---
        if proxy_string:
            proxy_dict = build_proxy_dict(proxy_string)
            if proxy_dict is None:
                log.error(f"Invalid proxy format: '{proxy_string}'.")
                if proxy_id:
                    mark_proxy_as_bad(proxy_id)
                continue
            log.info(
                f"Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: User {user_id} using proxy "
                f"{proxy_dict['hostname']} and device '{device_profile['device_model']}'"
            )
        else:
            log.warning(f"Attempt {attempt + 1}/{MAX_PROXY_RETRIES}: No proxy available for user {user_id}. Proceeding without proxy.")

        # --- Attempt Connection and Send Code ---
        try:
            client = Client(
                f"user_session_{phone}_{attempt}",
                api_id=config.API_ID or device_profile.get('api_id'),
                api_hash=config.API_HASH or device_profile.get('api_hash'),
                device_model=device_profile.get('device_model'),
                system_version=device_profile.get('system_version'),
                lang_code=device_profile.get('lang_code'),
                in_memory=True,
                proxy=proxy_dict
            )
            context.user_data['pyrogram_client'] = client

            await client.connect()
            sent_code = await client.send_code(phone)
            context.user_data['phone_code_hash'] = sent_code.phone_code_hash
            await context.bot.send_message(user_id, _("A login code has been sent. Please send it here."))
            return  # Success

        except Forbidden as e:
            if "RECAPTCHA_CHECK" in str(e):
                log.warning(f"Login for user {user_id} blocked by reCAPTCHA.")

                # Format the device profile details for the user
                device_info = "\n".join([f"- {k}: {v}" for k, v in device_profile.items()])

                # Format the final message
                debug_message = (
                    _("Telegram has blocked this login attempt with a CAPTCHA. This can be due to the phone number or the server's IP. Please try again later or with a different phone number.") +
                    "\n\n--- 🐞 Debug Info ---\n" +
                    _("Proxy Used: `{proxy}`").format(proxy=proxy_string or _("None")) + "\n" +
                    _("Device Profile:") + f"\n<pre>{device_info}</pre>"
                )

                await context.bot.send_message(user_id, debug_message, parse_mode=ParseMode.HTML)
            else:
                log.error(f"An unexpected Forbidden error occurred while sending code for user {user_id}: {e}", exc_info=True)
                await context.bot.send_message(user_id, _("An unexpected error occurred. Please try again."))
            if client and client.is_connected:
                await client.disconnect()
            return # Stop the process

        except (Timeout, ConnectionError, OSError) as e:
            reason = "SOCKS5 authentication failed" if is_socks_auth_error(e) else str(e)
            log.warning(
                f"Proxy/Connection failed for user {user_id} on attempt {attempt + 1}/{MAX_PROXY_RETRIES}. "
                f"Proxy ID: {proxy_id}. Error: {reason}"
            )
            if proxy_id:
                mark_proxy_as_bad(proxy_id)
            if client and client.is_connected:
                await client.disconnect()
            if attempt < MAX_PROXY_RETRIES - 1:
                await asyncio.sleep(1)  # Wait a bit before retrying
            else:
                await context.bot.send_message(user_id, _("Failed to connect to Telegram after multiple attempts. Please check proxy settings and try again later."))

        except PhoneNumberInvalid:
            await context.bot.send_message(user_id, _("The phone number is invalid. Please try again."))
            if client and client.is_connected:
                await client.disconnect()
            return

        except Exception as e:
            if is_socks_auth_error(e):
                log.warning(
                    f"SOCKS5 authentication failed for user {user_id} on attempt "
                    f"{attempt + 1}/{MAX_PROXY_RETRIES}. Proxy ID: {proxy_id}."
                )
                if proxy_id:
                    mark_proxy_as_bad(proxy_id)
                if client and getattr(client, "is_connected", False):
                    await client.disconnect()
                if attempt < MAX_PROXY_RETRIES - 1:
                    await asyncio.sleep(1)
                    continue
                await context.bot.send_message(user_id, _("Failed to connect to Telegram after multiple attempts. Please check proxy settings and try again later."))
                return
            log.error(f"An unexpected error occurred while sending code for user {user_id}: {e}", exc_info=True)
            await context.bot.send_message(user_id, _("An unexpected error occurred. Please try again."))
            if client and client.is_connected:
                await client.disconnect()
            return

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
    device_profile_id = context.user_data['device_profile_id']
    session_string = await client.export_session_string()
    await client.disconnect()
    if add_managed_account(user_id, phone, session_string, device_profile_id):
        await context.bot.send_message(
            user_id,
            _(
                "✅ Account added successfully!\n\n"
                "Group creation is <b>disabled</b> by default. Open /my_accounts to enable group creation or login-code monitoring for this account."
            ),
            parse_mode=ParseMode.HTML,
        )
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

# --- Two-Step Verification Conversation ---
TWO_STEP_NEW, TWO_STEP_CONFIRM, TWO_STEP_CURRENT, TWO_STEP_NEW_CHANGE, TWO_STEP_CONFIRM_CHANGE = range(10, 15)


def _clear_two_step_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in list(context.user_data.keys()):
        if str(key).startswith("two_step_"):
            context.user_data.pop(key, None)


def _two_step_error_text(code: str, _, detail: str | None = None) -> str:
    messages = {
        "empty": _("Please send a non-empty password."),
        "too_short": _("The password must be at least 4 characters."),
        "too_long": _("The password is too long. Please choose a shorter one."),
        "invalid": _("That password is not valid. Please send it as a single line of text."),
        "wrong_current": _("The current password is incorrect. Please try again."),
        "too_fresh": _("Telegram temporarily blocked changing this password. Please try again later."),
        "flood_wait": _("Telegram asked us to wait. Please try again in a few minutes."),
        "session_invalid": _("The account session is invalid or revoked. Please delete and re-add the account."),
        "already_enabled": _("Two-step verification is already enabled. Send the current password to change it."),
        "not_enabled": _("Two-step verification is not enabled on this account yet."),
        "current_required": _("This account already has a password. Send the current password first."),
        "account_unavailable": _("Error: Account not found or you don't have permission."),
        "connect_failed": _("Failed to connect to Telegram. Please try again later."),
    }
    text = messages.get(code, _("Could not update two-step verification. Please try again later."))
    if code == "flood_wait" and detail:
        text = _("Telegram asked us to wait {seconds} seconds. Please try again later.").format(seconds=detail)
    return text


def _two_step_back_markup(account_id: int, _):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")]]
    )


async def two_step_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)

    try:
        account_id = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text(_("Error: Account not found or you don't have permission."))
        return ConversationHandler.END

    if not user_owns_account(account_id, user_id):
        await query.edit_message_text(_("Error: Account not found or you don't have permission."))
        return ConversationHandler.END

    acc = get_account_details(account_id)
    phone = acc["phone"] if acc else "?"
    _clear_two_step_data(context)
    context.user_data["two_step_account_id"] = account_id
    context.user_data["two_step_phone"] = phone

    cancel_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(_("❌ Cancel"), callback_data="cancel_2fa")]]
    )
    await query.edit_message_text(
        _("Checking two-step verification status for <code>{phone}</code>...").format(phone=phone),
        parse_mode=ParseMode.HTML,
    )

    try:
        status = await get_two_step_status(account_id)
    except TwoStepError as e:
        await query.edit_message_text(
            _two_step_error_text(e.code, _, e.detail),
            reply_markup=_two_step_back_markup(account_id, _),
        )
        _clear_two_step_data(context)
        return ConversationHandler.END

    context.user_data["two_step_has_password"] = status["has_password"]
    hint = status.get("hint") or ""

    if status["has_password"]:
        hint_line = _("\nCurrent hint: <code>{hint}</code>").format(hint=hint) if hint else ""
        await query.edit_message_text(
            _(
                "<b>Two-Step Verification</b> is already enabled for <code>{phone}</code>.{hint_line}\n\n"
                "Send the <b>current</b> password to change it, or tap Cancel."
            ).format(phone=phone, hint_line=hint_line),
            reply_markup=cancel_markup,
            parse_mode=ParseMode.HTML,
        )
        return TWO_STEP_CURRENT

    await query.edit_message_text(
        _(
            "<b>Two-Step Verification</b> is not enabled for <code>{phone}</code>.\n\n"
            "Send the new password you want to set (at least 4 characters)."
        ).format(phone=phone),
        reply_markup=cancel_markup,
        parse_mode=ParseMode.HTML,
    )
    return TWO_STEP_NEW


async def two_step_receive_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    password = update.message.text or ""
    error = validate_two_step_password(password)
    if error:
        await update.message.reply_text(_two_step_error_text(error, _))
        return TWO_STEP_NEW
    context.user_data["two_step_new"] = password
    await update.message.reply_text(
        _("Please send the same password again to confirm."),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(_("❌ Cancel"), callback_data="cancel_2fa")]]
        ),
    )
    return TWO_STEP_CONFIRM


async def two_step_receive_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    confirm = update.message.text or ""
    expected = context.user_data.get("two_step_new")
    if confirm != expected:
        context.user_data.pop("two_step_new", None)
        await update.message.reply_text(
            _("The passwords do not match. Please send the new password again.")
        )
        return TWO_STEP_NEW
    return await _two_step_apply_and_finish(update, context, current_password=None)


async def two_step_receive_current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    current = update.message.text or ""
    if current == "":
        await update.message.reply_text(_("Please send a non-empty password."))
        return TWO_STEP_CURRENT
    context.user_data["two_step_current"] = current
    await update.message.reply_text(
        _("Send the <b>new</b> password (at least 4 characters)."),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(_("❌ Cancel"), callback_data="cancel_2fa")]]
        ),
    )
    return TWO_STEP_NEW_CHANGE


async def two_step_receive_new_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    password = update.message.text or ""
    error = validate_two_step_password(password)
    if error:
        await update.message.reply_text(_two_step_error_text(error, _))
        return TWO_STEP_NEW_CHANGE
    context.user_data["two_step_new"] = password
    await update.message.reply_text(_("Please send the same new password again to confirm."))
    return TWO_STEP_CONFIRM_CHANGE


async def two_step_receive_confirm_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    confirm = update.message.text or ""
    expected = context.user_data.get("two_step_new")
    if confirm != expected:
        context.user_data.pop("two_step_new", None)
        await update.message.reply_text(
            _("The passwords do not match. Please send the new password again.")
        )
        return TWO_STEP_NEW_CHANGE
    return await _two_step_apply_and_finish(
        update, context, current_password=context.user_data.get("two_step_current")
    )


async def _two_step_apply_and_finish(
    update: Update, context: ContextTypes.DEFAULT_TYPE, current_password: str | None
) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    account_id = context.user_data.get("two_step_account_id")
    new_password = context.user_data.get("two_step_new")
    phone = context.user_data.get("two_step_phone") or "?"

    if not account_id or not new_password:
        await update.message.reply_text(_("Could not update two-step verification. Please try again later."))
        _clear_two_step_data(context)
        return ConversationHandler.END

    await update.message.reply_text(_("Updating two-step verification, please wait..."))
    try:
        action = await apply_two_step_password(
            account_id, new_password, current_password=current_password
        )
    except TwoStepError as e:
        if e.code == "wrong_current":
            context.user_data.pop("two_step_current", None)
            context.user_data.pop("two_step_new", None)
            await update.message.reply_text(_two_step_error_text(e.code, _, e.detail))
            return TWO_STEP_CURRENT
        await update.message.reply_text(
            _two_step_error_text(e.code, _, e.detail),
            reply_markup=_two_step_back_markup(account_id, _),
        )
        _clear_two_step_data(context)
        return ConversationHandler.END

    if action == "changed":
        text = _("✅ Two-step verification password updated for <code>{phone}</code>.").format(phone=phone)
    else:
        text = _("✅ Two-step verification password enabled for <code>{phone}</code>.").format(phone=phone)

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_two_step_back_markup(account_id, _),
    )
    _clear_two_step_data(context)
    return ConversationHandler.END


async def two_step_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_two_step_data(context)
    try:
        chat = update.effective_chat if update else None
        user = update.effective_user if update else None
        if chat and user:
            _ = get_translation_func_for_user(user.id)
            await context.bot.send_message(chat.id, _("Two-step verification update cancelled."))
    except Exception:
        log.debug("Could not notify user about two-step conversation timeout.", exc_info=True)
    return ConversationHandler.END


async def cancel_two_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    account_id = context.user_data.get("two_step_account_id")
    _clear_two_step_data(context)

    query = update.callback_query
    text = _("Two-step verification update cancelled.")
    markup = _two_step_back_markup(account_id, _) if account_id else None
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)
    return ConversationHandler.END


two_step_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(two_step_start, pattern=r"^mng_2fa_\d+$"),
    ],
    states={
        TWO_STEP_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, two_step_receive_new)],
        TWO_STEP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, two_step_receive_confirm)],
        TWO_STEP_CURRENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, two_step_receive_current)],
        TWO_STEP_NEW_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, two_step_receive_new_change)],
        TWO_STEP_CONFIRM_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, two_step_receive_confirm_change)],
        ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, two_step_timeout)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_two_step),
        CallbackQueryHandler(cancel_two_step, pattern="^cancel_2fa$"),
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
            status_icon = "⚪️"  # Both features off
            if session_is_invalid(acc):
                status_icon = "❌"
            elif acc['last_error']:
                status_icon = "⚠️"
            elif acc['is_active']:
                status_icon = "🟢"
                if acc['next_creation_time']:
                    try:
                        next_time = datetime.fromisoformat(acc['next_creation_time'])
                        if next_time > datetime.now(timezone.utc):
                            status_icon = "🕒"
                    except (ValueError, TypeError):
                        pass
            if acc.get('code_monitor_enabled'):
                status_icon = f"{status_icon}🔐"

            identity = get_cached_identity(acc['id'])
            label = display_name(identity, acc['phone'])
            if session_is_invalid(acc):
                label = f"{label} · {_('invalid')}"
            button_text = f"{status_icon} {label}"
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"mng_select_{acc['id']}")])

    buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
    reply_markup = InlineKeyboardMarkup(buttons)

    query = update.callback_query
    if query:
        try:
            await query.answer()  # Answer the callback query
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.error(f"Error editing message in my_accounts_handler: {e}", exc_info=True)
            try:
                # As a fallback, try to send a new message with the error
                await context.bot.send_message(chat_id=user_id, text=f"An error occurred: {e}")
            except Exception as inner_e:
                log.error(f"Failed to send error message to user: {inner_e}")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

def _format_group_report(owned_groups, normal_groups_count, supergroups_count, upgradable_groups, account_id, _):
    """Formats the group report text and buttons from provided data."""
    text = _("<b>Group Ownership Report</b>\n\n")
    text += _("<b>Total Owned Groups:</b> {count}\n").format(count=owned_groups)
    text += _("- Normal Groups: {count}\n").format(count=normal_groups_count)
    text += _("- Supergroups: {count}\n\n").format(count=supergroups_count)

    buttons = []
    if upgradable_groups:
        text += _("You can upgrade your normal groups to supergroups below:")
        for chat in upgradable_groups:
            # The cached object might be a dict, so we access items with []
            chat_id = chat['id'] if isinstance(chat, dict) else chat.id
            chat_title = chat['title'] if isinstance(chat, dict) else chat.title

            btn_text = _("Upgrade '{title}'").format(title=chat_title)
            callback_data = f"mng_upgradegroup_{account_id}_{chat_id}"
            buttons.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

    buttons.append([InlineKeyboardButton(_("🔙 Back to Account"), callback_data=f"mng_select_{account_id}")])
    return text, InlineKeyboardMarkup(buttons)


async def account_detail_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int, message_id: int):
    """Displays the management menu for a single account."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)

    if not user_owns_account(account_id, user_id):
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=_("Error: Account not found or you don't have permission."),
        )
        return

    acc = get_account_details(account_id)

    if not acc:
        await context.bot.edit_message_text(chat_id=user_id, message_id=message_id, text=_("Error: Account not found or you don't have permission."))
        return

    identity = await load_identity_for_menu(account_id, acc)
    acc = get_account_details(account_id) or acc

    # Determine group-creation status string
    status_str = _("⚪️ Off")
    if session_is_invalid(acc):
        status_str = _("❌ Invalid")
    elif acc['last_error'] and acc['is_active']:
        status_str = _("⚠️ Error")
    elif acc['is_active']:
        status_str = _("🟢 On")
        if acc['next_creation_time']:
            try:
                # We still need to parse it to see if it's in the future for the status
                next_time = datetime.fromisoformat(acc['next_creation_time'])
                if next_time.tzinfo is None:
                    next_time = next_time.replace(tzinfo=timezone.utc)
                if next_time > datetime.now(timezone.utc):
                    status_str = _("🕒 Waiting")
            except (ValueError, TypeError):
                pass

    monitor_on = bool(acc.get('code_monitor_enabled'))
    if session_is_invalid(acc):
        monitor_str = _("❌ Invalid — sign in again")
    elif monitor_on:
        if code_monitor_manager.is_connected(account_id):
            monitor_str = _("🟢 On (connected)")
        else:
            monitor_str = _("🟡 On (connecting)")
    else:
        monitor_str = _("⚪️ Off")

    from html import escape as html_escape

    name = display_name(identity, acc["phone"])
    text = _("👤 <b>{name}</b>").format(name=html_escape(name))
    username = (identity or {}).get("username")
    if username:
        text += "\n" + _("🔗 @{username}").format(username=html_escape(username))
    text += "\n" + _("📱 <code>{phone}</code>").format(phone=html_escape(str(acc["phone"])))
    if identity and identity.get("user_id"):
        text += "\n" + _("🆔 <code>{user_id}</code>").format(user_id=identity["user_id"])
    if identity and identity.get("is_premium"):
        text += "\n" + _("⭐ Telegram Premium")

    text += format_session_health_text(acc, _)

    text += "\n"
    text += _("\n<b>Group Creation:</b> {status}").format(status=status_str)
    text += _("\n<b>Code Monitor:</b> {status}").format(status=monitor_str)

    proxy_host = None
    if acc.get("proxy_string"):
        parsed_proxy = build_proxy_dict(acc["proxy_string"])
        proxy_host = parsed_proxy["hostname"] if parsed_proxy else str(acc["proxy_string"]).split(":")[0]
    if proxy_host:
        proxy_label = proxy_host
    elif acc.get("proxy_id"):
        proxy_label = f"#{acc['proxy_id']}"
    else:
        proxy_label = _("None")
    text += _("\n<b>Proxy:</b> <code>{proxy}</code>").format(proxy=proxy_label)

    text += _("\n<b>Last Group:</b> {time}").format(time=_format_datetime(acc['last_creation_time']))
    text += _("\n<b>Next Group:</b> {time}").format(time=_format_datetime(acc['next_creation_time']))

    if acc['last_error'] and not session_is_invalid(acc):
        text += _("\n<b>Last Error:</b> <pre>{error}</pre>").format(error=acc['last_error'])

    text += _("\n\n📊 <b>Total Groups Created:</b> {count}").format(count=acc['total_groups'])

    buttons = [
        [
            InlineKeyboardButton(_("👤 View Profile"), callback_data=f"mng_profile_{acc['id']}"),
            InlineKeyboardButton(_("💬 Private Chats"), callback_data=f"mng_inbox_{acc['id']}"),
        ],
        [
            InlineKeyboardButton(
                _("▶️ Enable Group Creation") if not acc['is_active'] else _("⏹️ Disable Group Creation"),
                callback_data=f"mng_toggle_{acc['id']}"
            ),
            InlineKeyboardButton(_("🔄 Change Proxy"), callback_data=f"mng_proxy_{acc['id']}"),
        ],
        [
            InlineKeyboardButton(
                _("🔐 Enable Code Monitor") if not monitor_on else _("🔓 Disable Code Monitor"),
                callback_data=f"mng_monitor_{acc['id']}"
            ),
        ],
        [
            InlineKeyboardButton(
                _("🔑 Two-Step Verification"),
                callback_data=f"mng_2fa_{acc['id']}"
            ),
        ],
        [
            InlineKeyboardButton(_("📂 View Groups"), callback_data=f"mng_viewgroups_{acc['id']}"),
            InlineKeyboardButton(_("📊 Group Report"), callback_data=f"mng_groupreport_{acc['id']}"),
        ],
        [InlineKeyboardButton(_("❌ Delete"), callback_data=f"mng_delete_{acc['id']}")],
        [InlineKeyboardButton(_("🔙 Back to Account List"), callback_data="mng_back_list")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await _safe_edit_message_text(
        context.bot,
        chat_id=user_id,
        message_id=message_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )


async def async_generate_group_report(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: int):
    """Generates the group report in the background and edits the original message."""
    query = update.callback_query
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)

    session_string = get_account_session_string(account_id)
    if not session_string:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=query.message.message_id,
            text=_("Error: Could not retrieve session for this account.")
        )
        return

    device_profile = get_device_profile_by_account_id(account_id)
    if not device_profile:
        log.warning(f"No device profile found for account {account_id}. Using default client settings.")
        client = Client(f"user_session_reporter_{account_id}", session_string=session_string, in_memory=True, api_id=config.API_ID, api_hash=config.API_HASH)
    else:
        client = Client(
            f"user_session_reporter_{account_id}",
            session_string=session_string,
            api_id=config.API_ID or device_profile.get('api_id'),
            api_hash=config.API_HASH or device_profile.get('api_hash'),
            device_model=device_profile.get('device_model'),
            system_version=device_profile.get('system_version'),
            app_version=device_profile.get('app_version'),
            lang_code=device_profile.get('lang_code'),
            in_memory=True
        )

    try:
        await client.connect()

        owned_groups = 0
        normal_groups_count = 0
        supergroups_count = 0
        upgradable_groups = []

        async for dialog in client.get_dialogs():
            if dialog.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
                continue

            try:
                member = await client.get_chat_member(dialog.chat.id, "me")
                if member.status == ChatMemberStatus.OWNER:
                    owned_groups += 1
                    if dialog.chat.type == ChatType.GROUP:
                        normal_groups_count += 1
                        upgradable_groups.append(dialog.chat)
                    else:
                        supergroups_count += 1
            except Exception as e:
                log.warning(f"Could not get member status for chat {dialog.chat.id} ({dialog.chat.title}): {e}")
                continue

        await client.disconnect()

        # Convert chat objects to simple dicts for safer caching
        upgradable_groups_data = [{'id': chat.id, 'title': chat.title} for chat in upgradable_groups]

        # Cache the results
        cache_key = f"group_report_cache_{account_id}"
        context.bot_data[cache_key] = {
            'timestamp': datetime.now(timezone.utc),
            'owned_groups': owned_groups,
            'normal_groups_count': normal_groups_count,
            'supergroups_count': supergroups_count,
            'upgradable_groups': upgradable_groups_data
        }

        text, reply_markup = _format_group_report(
            owned_groups, normal_groups_count, supergroups_count, upgradable_groups_data, account_id, _
        )

        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=query.message.message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        log.error(f"Error generating group report for user {user_id}, account {account_id}: {e}", exc_info=True)
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=query.message.message_id,
            text=_("An error occurred while generating the report. The session might be invalid or revoked.")
        )
        if client.is_connected:
            await client.disconnect()


async def manage_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for all management callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    log.info(f"User {user_id} triggered manage_account_callback with data: {query.data}")

    try:
        _ = get_translation_func_for_user(user_id)
        from datetime import timedelta
        action_parts = query.data.split("_")
        action = action_parts[1] if len(action_parts) > 1 else ""
        if action not in {"proxy", "toggle", "monitor", "deleteconfirm"}:
            await query.answer()

        if action == "cancel":
            log.info(f"User {user_id} cancelled management action.")
            await query.message.delete()
            await context.bot.answer_callback_query(query.id, _("Cancelled."))
            return

        if action == "back":
            await my_accounts_handler(update, context)
            return

        if action == "select":
            account_id = int(action_parts[2])
            await account_detail_menu(update, context, account_id, query.message.message_id)
            return

        if action == "profile":
            await show_profile(update, context, int(action_parts[2]))
            return
        if action == "prefresh":
            await show_profile(update, context, int(action_parts[2]), force=True)
            return
        if action == "inbox":
            account_id = int(action_parts[2])
            page = int(action_parts[3]) if len(action_parts) > 3 else 0
            await show_inbox(update, context, account_id, page)
            return
        if action == "irefresh":
            await show_inbox(update, context, int(action_parts[2]), 0, force=True)
            return
        if action == "dm":
            await show_conversation(
                update,
                context,
                int(action_parts[2]),
                int(action_parts[3]),
                int(action_parts[4]) if len(action_parts) > 4 else 0,
            )
            return
        if action == "drefresh":
            await show_conversation(
                update,
                context,
                int(action_parts[2]),
                int(action_parts[3]),
                0,
                force=True,
            )
            return

        if action == "viewgroups":
            account_id = int(action_parts[2])
            page = int(action_parts[3]) if len(action_parts) > 3 else 0
            if not user_owns_account(account_id, user_id):
                await query.answer(_("Error: Account not found or you don't have permission."), show_alert=True)
                return
            log.info(f"User {user_id} requested to view groups for account {account_id} on page {page}.")

            await query.edit_message_text(_("Fetching groups... Please wait."))

            session_string = get_account_session_string(account_id)
            if not session_string:
                await query.edit_message_text(_("Error: Could not retrieve session for this account."))
                return

            device_profile = get_device_profile_by_account_id(account_id)
            if not device_profile:
                log.warning(f"No device profile found for account {account_id}. Using default client settings.")
                client = Client(f"user_session_reader_{account_id}", session_string=session_string, in_memory=True, api_id=config.API_ID, api_hash=config.API_HASH)
            else:
                client = Client(
                    f"user_session_reader_{account_id}",
                    session_string=session_string,
                    api_id=config.API_ID or device_profile.get('api_id'),
                    api_hash=config.API_HASH or device_profile.get('api_hash'),
                    device_model=device_profile.get('device_model'),
                    system_version=device_profile.get('system_version'),
                    app_version=device_profile.get('app_version'),
                    lang_code=device_profile.get('lang_code'),
                    in_memory=True
                )

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
        elif action == "groupreport":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} requested group report for account {account_id}.")

            cache_key = f"group_report_cache_{account_id}"
            cached_report = context.bot_data.get(cache_key)

            # Check if a valid cache exists (e.g., within 5 minutes)
            if cached_report and (datetime.now(timezone.utc) - cached_report.get('timestamp', datetime.min.replace(tzinfo=timezone.utc))) < timedelta(minutes=5):
                log.info(f"Using cached group report for account {account_id}.")
                text, reply_markup = _format_group_report(
                    cached_report['owned_groups'],
                    cached_report['normal_groups_count'],
                    cached_report['supergroups_count'],
                    cached_report['upgradable_groups'],
                    account_id,
                    _
                )
                await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                return  # Stop here, don't run the background task

            # Immediately confirm and notify the user that the task is running in the background.
            await query.edit_message_text(
                _("Generating your group report in the background. This may take a moment as it can be a slow operation. The message will be updated when ready...")
            )

            # Run the long-running task in the background
            asyncio.create_task(async_generate_group_report(update, context, account_id))
        elif action == "upgradegroup":
            account_id = int(action_parts[2])
            chat_id = int(action_parts[3])
            log.info(f"User {user_id} requested to upgrade group {chat_id} for account {account_id}.")

            await query.edit_message_text(_("Attempting to upgrade group..."))

            session_string = get_account_session_string(account_id)
            if not session_string:
                await query.edit_message_text(_("Error: Could not retrieve session for this account."))
                return

            device_profile = get_device_profile_by_account_id(account_id)
            if not device_profile:
                log.warning(f"No device profile found for account {account_id}. Using default client settings.")
                client = Client(f"user_session_upgrader_{account_id}", session_string=session_string, in_memory=True, api_id=config.API_ID, api_hash=config.API_HASH)
            else:
                client = Client(
                    f"user_session_upgrader_{account_id}",
                    session_string=session_string,
                    api_id=config.API_ID or device_profile.get('api_id'),
                    api_hash=config.API_HASH or device_profile.get('api_hash'),
                    device_model=device_profile.get('device_model'),
                    system_version=device_profile.get('system_version'),
                    app_version=device_profile.get('app_version'),
                    lang_code=device_profile.get('lang_code'),
                    in_memory=True
                )

            try:
                await client.connect()

                # messages.MigrateChat requires the positive group ID.
                if chat_id > 0:
                    # This is a safeguard, but chat IDs from pyrogram for groups are typically negative.
                    raise ValueError("chat_id for a basic group should be negative")

                await client.invoke(
                    pyrogram.raw.functions.messages.MigrateChat(
                        chat_id=-chat_id
                    )
                )

                # Smartly update the cache instead of invalidating it
                cache_key = f"group_report_cache_{account_id}"
                cached_report = context.bot_data.get(cache_key)
                if cached_report:
                    # Find and remove the upgraded group from the list
                    upgraded_group_found = False
                    for i, group in enumerate(cached_report['upgradable_groups']):
                        if group['id'] == chat_id:
                            cached_report['upgradable_groups'].pop(i)
                            upgraded_group_found = True
                            break

                    # Update the counts
                    if upgraded_group_found:
                        cached_report['normal_groups_count'] -= 1
                        cached_report['supergroups_count'] += 1
                        # Save the updated cache back
                        context.bot_data[cache_key] = cached_report
                        log.info(f"Updated group report cache for account {account_id} after upgrade.")

                await client.disconnect()

                text = _("✅ Group has been successfully upgraded to a Supergroup!")
                await context.bot.answer_callback_query(query.id, _("Success!"), show_alert=False)

            except Exception as e:
                log.error(f"Error upgrading group {chat_id} for user {user_id}, account {account_id}: {e}", exc_info=True)
                text = _("❌ An error occurred while upgrading the group: {error}").format(error=str(e))
                if client.is_connected:
                    await client.disconnect()

            # Button to go back to the report
            buttons = [[InlineKeyboardButton(_("🔙 Back to Group Report"), callback_data=f"mng_groupreport_{account_id}")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
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
                await context.bot.answer_callback_query(query.id, _("Group creation has been {status}.").format(status=status_text))
                # Refresh the menu
                await account_detail_menu(update, context, account_id, query.message.message_id)
            else:
                await context.bot.answer_callback_query(query.id, _("Could not change status."), show_alert=True)
        elif action == "monitor":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} toggled code monitor for account {account_id}.")
            new_status = toggle_code_monitor(account_id, user_id)
            if new_status is None:
                await context.bot.answer_callback_query(query.id, _("Could not change status."), show_alert=True)
            elif new_status:
                details = get_user_details(user_id)
                has_sub = bool(details and details.get('subscription'))
                if not has_sub:
                    await context.bot.answer_callback_query(
                        query.id,
                        _("Code monitor is enabled, but an active subscription is required to run it."),
                        show_alert=True,
                    )
                else:
                    code_monitor_manager.set_bot(context.bot)
                    started = await code_monitor_manager.on_enabled(account_id)
                    if started:
                        await context.bot.answer_callback_query(
                            query.id,
                            _("Code monitor enabled. Login codes and security notices will be forwarded here."),
                        )
                    else:
                        await context.bot.answer_callback_query(
                            query.id,
                            _("Code monitor is enabled, but the account could not connect yet. It will retry automatically."),
                            show_alert=True,
                        )
                await account_detail_menu(update, context, account_id, query.message.message_id)
            else:
                await code_monitor_manager.stop_account(account_id)
                await context.bot.answer_callback_query(query.id, _("Code monitor disabled."))
                await account_detail_menu(update, context, account_id, query.message.message_id)
        elif action == "proxy":
            account_id = int(action_parts[2])
            log.info(f"User {user_id} reassigned proxy for account {account_id}.")
            success, msg_key = reassign_proxy(account_id, user_id)
            proxy_messages = {
                "proxy_update_success": _("✅ Proxy updated."),
                "no_available_proxies": _("No available proxies."),
                "db_error": _("Could not update the proxy."),
            }
            toast = proxy_messages.get(msg_key, _("Could not update the proxy."))
            if not success and msg_key == "proxy_update_success":
                toast = _("Could not update the proxy.")
            await query.answer(toast, show_alert=True)
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
            await code_monitor_manager.stop_account(account_id)
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
                        status_icon = "⚪️"
                        if acc['is_active']:
                            status_icon = "🟢"
                        if acc.get('code_monitor_enabled'):
                            status_icon = f"{status_icon}🔐"
                        label = display_name(get_cached_identity(acc['id']), acc['phone'])
                        button_text = f"{status_icon} {label}"
                        buttons.append([InlineKeyboardButton(button_text, callback_data=f"mng_select_{acc['id']}")])
                buttons.append([InlineKeyboardButton(_("🔙 Back"), callback_data='main_back')])
                reply_markup = InlineKeyboardMarkup(buttons)
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            else:
                await query.edit_message_text(_("❌ Could not delete account."))
    except Exception as e:
        if _is_message_not_modified(e):
            log.debug(f"Ignored unchanged account menu edit for user {user_id}.")
            return
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
    CallbackQueryHandler(select_plan_handler, pattern="^select_plan_"),
    CallbackQueryHandler(pay_with_stars_callback, pattern="^pay_stars_"),
    CallbackQueryHandler(pay_with_crypto_callback, pattern="^pay_crypto_"),
    PreCheckoutQueryHandler(precheckout_callback),
    MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback),
    CommandHandler("language", language_handler),
    CallbackQueryHandler(set_language_callback, pattern="^set_lang_"),
    CommandHandler("my_accounts", my_accounts_handler),
    two_step_conv_handler,
    CallbackQueryHandler(manage_account_callback, pattern="^mng_"),
    CallbackQueryHandler(info_page_callback, pattern="^info_"),
    CallbackQueryHandler(main_menu_callback, pattern="^main_"),
    add_account_conv_handler,
]
