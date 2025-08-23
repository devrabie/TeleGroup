import logging
import asyncio
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
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
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordRequired
)

import config
from database import (
    get_all_plans, get_plan_by_id, grant_subscription, get_user_details, add_managed_account,
    delete_managed_account, toggle_account_status, reassign_proxy, get_account_stats,
    set_user_language
)
from translation import get_translation_func_for_user

log = logging.getLogger(__name__)

# --- Helper function to run Pyrogram logic in a separate thread ---

def run_pyrogram_task(target, args):
    def thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(target(*args))
        finally:
            loop.close()
    thread = threading.Thread(target=thread_target)
    thread.start()

# --- Handlers for various bot features ---

async def subscribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    plans = get_all_plans(active_only=True)
    if not plans:
        await update.message.reply_text(_("There are currently no subscription plans available. Please check back later."))
        return
    buttons = [[InlineKeyboardButton(
        _("{plan_name} - {price} Stars").format(plan_name=p['name'], price=p['price_stars']),
        callback_data=f"select_plan_{p['id']}"
    )] for p in plans]
    await update.message.reply_text(
        _("Please select a subscription plan from the list below:"),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

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
    price = LabeledPrice(_("Subscription"), plan['price_stars'] * 100)
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
    _ = get_translation_func_for_user(update.effective_user.id)
    buttons = [[InlineKeyboardButton("English 🇬🇧", callback_data="set_lang_en")], [InlineKeyboardButton("العربية 🇸🇦", callback_data="set_lang_ar")]]
    await update.message.reply_text(_("Please choose your language:"), reply_markup=InlineKeyboardMarkup(buttons))

async def set_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[2]
    user_id = query.from_user.id
    set_user_language(user_id, lang_code)
    _new = get_translation_func_for_user(user_id)
    await query.edit_message_text(_new("Language changed successfully."))

# --- Add Account Conversation ---
PHONE, CODE, PASSWORD = range(3)

async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    details = get_user_details(user_id)
    if not (details and details.get('subscription')):
        await update.message.reply_text(_("You need an active subscription to add accounts. Use /subscribe to get one."))
        return ConversationHandler.END
    plan = get_plan_by_id(details['subscription']['plan_id'])
    if len(details['accounts']) >= plan['max_accounts']:
        await update.message.reply_text(_("You have reached the maximum of {max_accounts} accounts for your '{plan_name}' plan.").format(
            max_accounts=plan['max_accounts'], plan_name=plan['name']))
        return ConversationHandler.END
    await update.message.reply_text(_("Please send the phone number of the account you want to add.\n<i>(Must be in international format, e.g., +1234567890)</i>"))
    return PHONE

async def async_send_code(phone, context, user_id, _):
    client = Client(f"user_session_{phone}", api_id=config.API_ID, api_hash=config.API_HASH, in_memory=True)
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
    context.user_data['phone'] = update.message.text
    await update.message.reply_text(_("Processing... Please wait."))
    run_pyrogram_task(target=async_send_code, args=(update.message.text, context, user_id, _))
    return CODE

async def async_sign_in(code, context, user_id, _):
    client = context.user_data['pyrogram_client']
    phone = context.user_data['phone']
    phone_code_hash = context.user_data['phone_code_hash']
    next_state = ConversationHandler.END
    try:
        await client.sign_in(phone, phone_code_hash, code)
        await async_complete_login(context, user_id, _)
    except SessionPasswordRequired:
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
    run_pyrogram_task(target=async_sign_in, args=(update.message.text, context, user_id, _))
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
    run_pyrogram_task(target=async_check_password, args=(update.message.text, context, user_id, _))
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
    _ = get_translation_func_for_user(update.effective_user.id)
    if 'pyrogram_client' in context.user_data:
        client = context.user_data['pyrogram_client']
        if client.is_connected:
            await client.disconnect()
    context.user_data.clear()
    await update.message.reply_text(_("Operation cancelled."))
    return ConversationHandler.END

add_account_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("add_account", add_account_start)],
    states={
        PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone_number)],
        CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone_code)],
        PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
    },
    fallbacks=[CommandHandler("cancel", cancel_conversation)],
    conversation_timeout=300
)

# --- User Dashboard ---

async def my_accounts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of the user's managed accounts with control buttons."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)
    details = get_user_details(user_id)

    if not details or not details['accounts']:
        await update.message.reply_text(_("You have not added any accounts yet. Use /add_account to get started."))
        return

    await update.message.reply_text(_("Your managed accounts:"))
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
        await update.message.reply_text(text, reply_markup=reply_markup)

async def manage_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for all management callbacks."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    _ = get_translation_func_for_user(user_id)

    action_parts = query.data.split("_")
    action = action_parts[1]

    if action == "cancel":
        await query.message.delete()
        await context.bot.answer_callback_query(query.id, _("Cancelled."))
        return

    account_id = int(action_parts[2])

    if action == "stats":
        total_groups = get_account_stats(account_id)
        await context.bot.answer_callback_query(query.id, _("This account has created {count} groups.").format(count=total_groups), show_alert=True)
    elif action == "toggle":
        new_status = toggle_account_status(account_id, user_id)
        if new_status is not None:
            status_text = _("activated") if new_status else _("deactivated")
            await context.bot.answer_callback_query(query.id, _("Account has been {status}.").format(status=status_text))
        else:
            await context.bot.answer_callback_query(query.id, _("Could not change status."), show_alert=True)
    elif action == "proxy":
        success, msg = reassign_proxy(account_id, user_id)
        await context.bot.answer_callback_query(query.id, msg, show_alert=True)
    elif action == "delete":
        buttons = [[InlineKeyboardButton(_("Yes, delete it"), callback_data=f"mng_deleteconfirm_{account_id}"), InlineKeyboardButton(_("No, cancel"), callback_data="mng_cancel")]]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    elif action == "deleteconfirm":
        if delete_managed_account(account_id, user_id):
            await query.edit_message_text(_("✅ Account has been deleted."))
        else:
            await query.edit_message_text(_("❌ Could not delete account."))

# --- Handler Registration ---
user_handlers_list = [
    CommandHandler("subscribe", subscribe_handler),
    CallbackQueryHandler(select_plan_callback, pattern="^select_plan_"),
    PreCheckoutQueryHandler(precheckout_callback),
    MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback),
    CommandHandler("language", language_handler),
    CallbackQueryHandler(set_language_callback, pattern="^set_lang_"),
    CommandHandler("my_accounts", my_accounts_handler),
    CallbackQueryHandler(manage_account_callback, pattern="^mng_"),
    add_account_conv_handler,
]
