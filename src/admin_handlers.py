import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, filters, CallbackQueryHandler, ConversationHandler, MessageHandler
from telegram.constants import ParseMode

from src import config
from src.database import (
    add_plan, get_all_plans, get_all_users, get_user_details, grant_subscription,
    get_system_stats, get_plan_by_id, update_plan
)
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

# --- Custom Filters ---
# In python-telegram-bot, filters are handled differently. We'll apply them when creating the CommandHandler.
admin_filter = filters.User(user_id=config.ADMIN_IDS)

# --- New Admin Panel Handlers ---

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main admin panel."""
    user_id = update.effective_user.id
    _ = get_translation_func_for_user(user_id)

    text = _("Welcome to the Admin Panel. Please choose a category to manage.")
    keyboard = [
        [InlineKeyboardButton(_("📊 Statistics"), callback_data='admin_view_stats')],
        [InlineKeyboardButton(_("👥 Manage Users"), callback_data='admin_menu_users')],
        [InlineKeyboardButton(_("📋 Manage Plans"), callback_data='admin_menu_plans')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def stats_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays system-wide statistics."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    stats = get_system_stats()

    if stats:
        text = _(
            "<b>📊 System Statistics</b>\n\n"
            "<b>Total Users:</b> {total_users}\n"
            "<b>Active Subscriptions:</b> {active_subscriptions}\n"
            "<b>Managed Accounts:</b> {total_managed_accounts}\n"
            "<b>Groups Created (Today):</b> {groups_created_today}\n"
            "<b>Groups Created (Total):</b> {groups_created_total}\n"
        ).format(**stats)
    else:
        text = _("Could not retrieve statistics.")

    keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def edit_plan_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays the menu for editing a single plan's details.
    Can either edit an existing message or send a new one.
    """
    _ = get_translation_func_for_user(update.effective_user.id)
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Determine the plan_id from context or a new query
    if 'edit_plan_id' in context.user_data:
        plan_id = context.user_data['edit_plan_id']
    elif query:
        plan_id = int(query.data.split('_')[-1])
        context.user_data['edit_plan_id'] = plan_id
        context.user_data['edit_menu_message_id'] = query.message.message_id
    else:
        # This can happen if context is lost.
        if update.message:
            await update.message.reply_text(_("Could not determine which plan to edit. Please start over."))
        return

    if query:
        await query.answer()

    plan = get_plan_by_id(plan_id)
    if not plan:
        # Handle plan not found
        context.user_data.pop('edit_plan_id', None)
        context.user_data.pop('edit_menu_message_id', None)
        error_text = _("Error: Plan not found. It might have been deleted.")
        if query:
            await query.edit_message_text(error_text)
        else:
            await context.bot.send_message(chat_id, error_text)
        return

    # Build the message text and keyboard
    status = _("Active") if plan['is_active'] else _("Inactive")
    price_usd_text = f"${plan['price_usd']:.2f}" if plan.get('price_usd') and plan['price_usd'] > 0 else "Not set"
    text = _(
        "<b>Editing Plan:</b> {name} (ID: <code>{id}</code>)\n\n"
        "Select a field to modify:\n\n"
        "<b>Name:</b> {name}\n"
        "<b>Price (Stars):</b> {price_stars}\n"
        "<b>Price (USD):</b> {price_usd}\n"
        "<b>Duration:</b> {duration} days\n"
        "<b>Max Accounts:</b> {max_accounts}\n"
        "<b>Daily Limit:</b> {limit} groups/day\n"
        "<b>Status:</b> {status}"
    ).format(
        id=plan['id'], name=plan['name'], price_stars=plan['price_stars'],
        price_usd=price_usd_text, duration=plan['duration_days'],
        max_accounts=plan['max_accounts'], limit=plan['daily_group_limit'], status=status
    )
    keyboard = [
        [
            InlineKeyboardButton(_("✏️ Name"), callback_data=f"edit_field_name"),
            InlineKeyboardButton(_("✨ Price (Stars)"), callback_data=f"edit_field_price_stars"),
        ],
        [
            InlineKeyboardButton(_("💵 Price (USD)"), callback_data=f"edit_field_price_usd"),
            InlineKeyboardButton(_("📅 Duration"), callback_data=f"edit_field_duration_days"),
        ],
        [
            InlineKeyboardButton(_("👤 Max Accounts"), callback_data=f"edit_field_max_accounts"),
            InlineKeyboardButton(_("📈 Daily Limit"), callback_data=f"edit_field_daily_group_limit"),
        ],
        [
            InlineKeyboardButton(_("Toggle Active/Inactive"), callback_data=f"edit_field_toggle_active"),
        ],
        [InlineKeyboardButton(_("🔙 Back to Plan List"), callback_data='admin_plan_edit_list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Try to edit the existing menu message, otherwise send a new one
    menu_message_id = context.user_data.get('edit_menu_message_id')
    if menu_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=menu_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception:
             # If editing fails (e.g., message too old), send a new message
            new_menu_message = await context.bot.send_message(
                chat_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
            context.user_data['edit_menu_message_id'] = new_menu_message.message_id
    else:
        # If we don't have a message ID, we must send a new one
        new_menu_message = await context.bot.send_message(
            chat_id, text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
        )
        context.user_data['edit_menu_message_id'] = new_menu_message.message_id


# --- Edit Plan Conversation Handlers ---
GET_NEW_VALUE = range(30, 31)

async def edit_field_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to edit a specific field."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    field_to_edit = query.data.replace("edit_field_", "")
    context.user_data['edit_field'] = field_to_edit

    # Provide a more user-friendly name for the field
    field_map = {
        "name": "Name",
        "price_stars": "Price (Stars)",
        "price_usd": "Price (USD)",
        "duration_days": "Duration (days)",
        "max_accounts": "Max Accounts",
        "daily_group_limit": "Daily Limit"
    }
    field_name = _(field_map.get(field_to_edit, field_to_edit))

    text = _("Please send the new value for <b>{field_name}</b>.\n\nSend /cancel to abort.").format(field_name=field_name)
    # We need to send a new message here because we can't get a text reply from a button press
    prompt_message = await query.message.reply_text(text, parse_mode=ParseMode.HTML)
    context.user_data['prompt_message_id'] = prompt_message.message_id
    return GET_NEW_VALUE

async def edit_field_receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the new value, updates the plan, and cleans up the chat."""
    _ = get_translation_func_for_user(update.effective_user.id)
    new_value = update.message.text
    field_to_edit = context.user_data.get('edit_field')
    plan_id = context.user_data.get('edit_plan_id')
    chat_id = update.effective_chat.id

    if not all([field_to_edit, plan_id]):
        await update.message.reply_text(_("An error occurred (missing context). Please start over."))
        return ConversationHandler.END

    # --- Validation and Type Conversion ---
    try:
        if field_to_edit in ["price_stars", "duration_days", "max_accounts", "daily_group_limit"]:
            processed_value = int(new_value)
        elif field_to_edit == "price_usd":
            processed_value = float(new_value.replace(',', '.')) # Allow comma as decimal separator
        else:
            processed_value = new_value
    except ValueError:
        await update.message.reply_text(_("Invalid value type. Please enter a valid number."))
        return GET_NEW_VALUE # Ask again

    # --- Update Database ---
    success, msg_key = update_plan(plan_id, **{field_to_edit: processed_value})

    # --- Clean up messages ---
    try:
        # Delete the user's reply
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        # Delete the bot's prompt
        if 'prompt_message_id' in context.user_data:
            await context.bot.delete_message(chat_id=chat_id, message_id=context.user_data['prompt_message_id'])
    except Exception as e:
        log.warning(f"Could not delete messages during plan edit: {e}")

    if not success:
        # If the update failed, we still need to tell the user.
        # The original menu will be shown again by the call below.
        if ":" in msg_key:
            key, value = msg_key.split(":", 1)
            error_message = _(key).format(value=value)
        else:
            error_message = _(msg_key)
        await context.bot.send_message(chat_id, f"❌ {error_message}")

    # --- Clean up context and show the updated menu ---
    context.user_data.pop('edit_field', None)
    context.user_data.pop('prompt_message_id', None)

    await edit_plan_menu_handler(update, context)
    return ConversationHandler.END

async def edit_field_toggle_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles the is_active status of a plan without a conversation."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)
    plan_id = context.user_data.get('edit_plan_id')

    if not plan_id:
        # If context is lost, try to get it from the callback data as a fallback
        # This is not ideal, but can prevent some errors.
        # A better solution would involve more robust state management.
        await query.edit_message_text(_("An error occurred (missing context). Please start over."))
        return

    plan = get_plan_by_id(plan_id)
    if not plan:
        await query.edit_message_text(_("Error: Plan not found."))
        return

    new_status = not plan['is_active']
    success, msg_key = update_plan(plan_id, is_active=new_status)

    if success:
        await context.bot.answer_callback_query(query.id, _("Status toggled successfully."))
    else:
        await context.bot.answer_callback_query(query.id, f"❌ {_(msg_key)}", show_alert=True)

    await edit_plan_menu_handler(update, context)

async def edit_conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the edit process, cleans up all context, and returns to the main admin panel."""
    _ = get_translation_func_for_user(update.effective_user.id)

    # Clean up all session data for this conversation
    context.user_data.pop('edit_field', None)
    context.user_data.pop('prompt_message_id', None)
    context.user_data.pop('edit_plan_id', None)
    context.user_data.pop('edit_menu_message_id', None)

    await update.message.reply_text(_("Edit operation cancelled. Returning to the main admin panel."))

    # Show the main admin panel to avoid leaving the user in a broken state
    await admin_panel_handler(update, context)
    return ConversationHandler.END

edit_plan_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(edit_field_start, pattern='^edit_field_')],
    states={
        GET_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_receive_value)],
    },
    fallbacks=[CommandHandler('cancel', edit_conv_cancel)],
    # We need to make sure this conversation doesn't block the main menu navigation
    block=False,
    per_message=False,
)

async def users_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the user management menu."""
    query = update.callback_query
    if query:
        await query.answer()

    _ = get_translation_func_for_user(update.effective_user.id)

    text = _("<b>Manage Users</b>\n\nSelect an option from below.")
    keyboard = [
        [InlineKeyboardButton(_("📜 List All Users"), callback_data='admin_users_list_0')],
        # [InlineKeyboardButton(_("🔎 Find User by ID"), callback_data='admin_user_find_start')], # Will implement later
        [InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


USERS_PER_PAGE = 10


async def users_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a paginated list of all users."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    page = int(query.data.split('_')[-1])

    users = get_all_users()
    if not users:
        text = _("No users found.")
        keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_users')]]
    else:
        start_index = page * USERS_PER_PAGE
        end_index = start_index + USERS_PER_PAGE
        paginated_users = users[start_index:end_index]

        text = _("<b>Bot Users (Page {page_num}):</b>\n\n").format(page_num=page + 1)

        keyboard = []
        for user in paginated_users:
            admin_badge = " (Admin)" if user['is_admin'] else ""
            # Add a button for each user to view their details
            keyboard.append([
                InlineKeyboardButton(
                    f"👤 {user['first_name']} ({user['telegram_id']})",
                    callback_data=f"admin_user_view_{user['telegram_id']}"
                )
            ])

        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"admin_users_list_{page - 1}"))
        if end_index < len(users):
            pagination_buttons.append(InlineKeyboardButton("➡️", callback_data=f"admin_users_list_{page + 1}"))

        if pagination_buttons:
            keyboard.append(pagination_buttons)

        keyboard.append([InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_users')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def user_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays details for a specific user, called from a button."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    user_id = int(query.data.split('_')[-1])
    details = get_user_details(user_id)

    if not details:
        text = _("User with ID <code>{user_id}</code> not found.").format(user_id=user_id)
        keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_users')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return

    user = details['user']
    sub = details['subscription']
    accounts = details['accounts']

    # Create a user mention string that is clickable
    if user.get('username'):
        user_mention = f"@{user['username']}"
    else:
        # Use HTML for a "mention" link if no username
        user_mention = f'<a href="tg://user?id={user["telegram_id"]}">{user["first_name"]}</a>'

    reply = _("<b>User Details for:</b> {user_mention} (<code>{user_id}</code>)\n").format(
        user_mention=user_mention, user_id=user['telegram_id']
    )
    is_admin_text = _("Yes") if user['is_admin'] else _("No")
    reply += _("<b>Admin:</b> {is_admin}\n").format(is_admin=is_admin_text)
    reply += _("<b>Language:</b> {lang}\n").format(lang=user['language_code'])
    reply += _("<b>Joined:</b> {join_date}\n").format(join_date=user['created_at'])
    reply += "--------------------\n"

    if sub:
        reply += (
            _("<b>Subscription:</b> {plan_name} (Plan ID: {plan_id})\n"
              "<b>Expires:</b> {end_date}\n").format(
                plan_name=sub['plan_name'], plan_id=sub['plan_id'], end_date=sub['end_date']
            )
        )
    else:
        reply += _("<b>Subscription:</b> None\n")

    reply += "--------------------\n"
    reply += _("<b>Managed Accounts ({count}):</b>\n").format(count=len(accounts))
    if accounts:
        for acc in accounts:
            status = _("Active") if acc['is_active'] else _("Inactive")
            reply += _("  - <code>{phone}</code> ({status})\n").format(phone=acc['phone'], status=status)
    else:
        reply += _("  None\n")

    keyboard = [
        [InlineKeyboardButton(_("🎁 Grant Subscription"), callback_data=f"admin_grant_start_{user_id}")],
        [InlineKeyboardButton(_("🔙 Back to User List"), callback_data='admin_users_list_0')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(reply, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A generic cancellation command for conversations."""
    _ = get_translation_func_for_user(update.effective_user.id)

    # Clean up any potential data stored in user_data
    context.user_data.pop('new_plan', None)
    context.user_data.pop('grant_sub_user_id', None)
    context.user_data.pop('grant_sub_plan_id', None)

    await update.message.reply_text(_("Operation cancelled."))

    # We don't know which menu to return to, so we just end.
    # A more advanced setup could store the "return menu" in user_data.
    return ConversationHandler.END


# --- Grant Subscription Conversation Handlers ---

(GRANT_CHOOSE_PLAN, GRANT_DURATION) = range(20, 22)


async def grant_sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the grant subscription conversation by showing available plans."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    user_id_to_grant = int(query.data.split('_')[-1])
    context.user_data['grant_sub_user_id'] = user_id_to_grant

    plans = get_all_plans(active_only=True)
    if not plans:
        await query.edit_message_text(_("There are no active plans to grant. Please create one first."))
        return ConversationHandler.END

    keyboard = []
    for plan in plans:
        keyboard.append([
            InlineKeyboardButton(
                _("{name} - {price} Stars").format(name=plan['name'], price=plan['price_stars']),
                callback_data=f"admin_grant_selectplan_{plan['id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton(_("❌ Cancel"), callback_data='admin_grant_cancel')])

    text = _("Please choose a plan to grant to user <code>{user_id}</code>:").format(user_id=user_id_to_grant)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    return GRANT_CHOOSE_PLAN


async def grant_sub_receive_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the chosen plan and asks for the duration."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    plan_id = int(query.data.split('_')[-1])
    context.user_data['grant_sub_plan_id'] = plan_id

    text = _("Please enter the duration for this subscription in days (e.g., 30).\n\nOr send /cancel to abort.")
    await query.edit_message_text(text)
    return GRANT_DURATION


async def grant_sub_receive_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the duration and confirms the grant."""
    _ = get_translation_func_for_user(update.effective_user.id)

    try:
        duration = int(update.message.text)
        user_id = context.user_data['grant_sub_user_id']
        plan_id = context.user_data['grant_sub_plan_id']

        success, msg_key = grant_subscription(user_id, plan_id, duration)

        if success:
            await update.message.reply_text(f"✅ {_(msg_key)}")
        else:
            await update.message.reply_text(f"❌ {_(msg_key)}")

    except (ValueError, KeyError):
        await update.message.reply_text(_("An error occurred. Please try again."))

    context.user_data.pop('grant_sub_user_id', None)
    context.user_data.pop('grant_sub_plan_id', None)

    # Can't easily return to the user view, so just end.
    return ConversationHandler.END


async def grant_sub_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the grant subscription process."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    context.user_data.pop('grant_sub_user_id', None)
    context.user_data.pop('grant_sub_plan_id', None)

    await query.edit_message_text(_("Grant subscription cancelled."))
    return ConversationHandler.END

grant_sub_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(grant_sub_start, pattern='^admin_grant_start_')],
    states={
        GRANT_CHOOSE_PLAN: [
            CallbackQueryHandler(grant_sub_receive_plan, pattern='^admin_grant_selectplan_'),
        ],
        GRANT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_sub_receive_duration)],
    },
    fallbacks=[
        CommandHandler('cancel', conv_cancel),
        CallbackQueryHandler(grant_sub_cancel, pattern='^admin_grant_cancel$')
    ],
    block=False,
    per_message=False,
)


async def plans_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the plan management menu."""
    query = update.callback_query
    if query:
        await query.answer()

    _ = get_translation_func_for_user(update.effective_user.id)

    text = _("<b>Manage Subscription Plans</b>\n\nSelect an option from below.")
    keyboard = [
        [InlineKeyboardButton(_("📜 List All Plans"), callback_data='admin_plans_list')],
        [InlineKeyboardButton(_("➕ Create New Plan"), callback_data='admin_plan_create_start')],
        [InlineKeyboardButton(_("✏️ Edit a Plan"), callback_data='admin_plan_edit_list')],
        [InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        # This case is unlikely to be hit in the admin panel flow but is good practice
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def plans_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of all subscription plans."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    plans = get_all_plans(active_only=False)
    if not plans:
        text = _("No subscription plans found.")
    else:
        text = _("<b>Existing Subscription Plans:</b>\n\n")
        for plan in plans:
            status = _("Active") if plan['is_active'] else _("Inactive")
            price_usd_text = f", <b>Price (USD):</b> ${plan['price_usd']:.2f}" if plan.get('price_usd') and plan['price_usd'] > 0 else ""
            text += (
                _("<b>ID:</b> <code>{id}</code>, <b>Name:</b> {name}\n"
                  "<b>Price (Stars):</b> {price}{price_usd_text}, <b>Duration:</b> {days} days\n"
                  "<b>Accounts:</b> {accounts}, <b>Limit:</b> {limit} groups/day\n"
                  "<b>Status:</b> {status}\n"
                  "--------------------\n").format(
                    id=plan['id'], name=plan['name'], price=plan['price_stars'],
                    price_usd_text=price_usd_text,
                    days=plan['duration_days'], accounts=plan['max_accounts'],
                    limit=plan['daily_group_limit'], status=status
                )
            )

    keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_plans')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def edit_plan_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays a list of all plans to choose from for editing."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    plans = get_all_plans(active_only=False)
    if not plans:
        text = _("No subscription plans found to edit.")
        keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_plans')]]
    else:
        text = _("Please select a plan to edit:")
        keyboard = []
        for plan in plans:
            keyboard.append([
                InlineKeyboardButton(
                    plan['name'],
                    callback_data=f"admin_plan_edit_{plan['id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_plans')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


# --- Create Plan Conversation Handlers ---

(PLAN_NAME, PLAN_PRICE, PLAN_PRICE_USD, PLAN_DURATION, PLAN_ACCOUNTS, PLAN_LIMIT, PLAN_CONFIRM) = range(10, 17)


async def plan_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the create plan conversation."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    context.user_data['new_plan'] = {}

    text = _("Let's create a new plan.\n\nFirst, what is the name of the plan? (e.g., 'Premium')\n\nYou can send /cancel at any time to stop.")
    await query.edit_message_text(text=text)
    return PLAN_NAME


async def plan_create_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the plan name and asks for the price."""
    _ = get_translation_func_for_user(update.effective_user.id)
    name = update.message.text
    context.user_data['new_plan']['name'] = name

    await update.message.reply_text(_("Great. Now, what is the price in Telegram Stars? (e.g., 100)"))
    return PLAN_PRICE


async def plan_create_receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the price and asks for the price in USD."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        price = int(update.message.text)
        context.user_data['new_plan']['price'] = price
        await update.message.reply_text(_("Next, what is the price in USD for crypto payments? (e.g., 5.99)"))
        return PLAN_PRICE_USD
    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the price in Stars again."))
        return PLAN_PRICE


async def plan_create_receive_price_usd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the USD price and asks for the duration."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        price_usd = float(update.message.text)
        context.user_data['new_plan']['price_usd'] = price_usd
        await update.message.reply_text(_("Perfect. How many days will the subscription last? (e.g., 30)"))
        return PLAN_DURATION
    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the price in USD again."))
        return PLAN_PRICE_USD


async def plan_create_receive_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the duration and asks for the max accounts."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        duration = int(update.message.text)
        context.user_data['new_plan']['duration'] = duration
        await update.message.reply_text(_("Got it. How many accounts can a user on this plan add? (e.g., 5)"))
        return PLAN_ACCOUNTS
    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the duration in days again."))
        return PLAN_DURATION


async def plan_create_receive_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the max accounts and asks for the daily group limit."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        accounts = int(update.message.text)
        context.user_data['new_plan']['accounts'] = accounts
        await update.message.reply_text(_("Almost done. What is the daily group creation limit for each account? (e.g., 20)"))
        return PLAN_LIMIT
    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the max accounts again."))
        return PLAN_ACCOUNTS


async def plan_create_receive_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the limit and shows a confirmation."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        limit = int(update.message.text)
        context.user_data['new_plan']['limit'] = limit

        plan_data = context.user_data['new_plan']
        text = _(
            "<b>Please confirm the new plan details:</b>\n\n"
            "<b>Name:</b> {name}\n"
            "<b>Price (Stars):</b> {price}\n"
            "<b>Price (USD):</b> {price_usd:.2f}\n"
            "<b>Duration:</b> {duration} days\n"
            "<b>Max Accounts:</b> {accounts}\n"
            "<b>Daily Limit:</b> {limit} groups/day"
        ).format(
            name=plan_data['name'],
            price=plan_data['price'],
            price_usd=plan_data['price_usd'],
            duration=plan_data['duration'],
            accounts=plan_data['accounts'],
            limit=plan_data['limit']
        )

        keyboard = [
            [InlineKeyboardButton(_("✅ Save Plan"), callback_data='admin_plan_create_save')],
            [InlineKeyboardButton(_("❌ Cancel"), callback_data='admin_plan_create_cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return PLAN_CONFIRM

    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the daily group limit again."))
        return PLAN_LIMIT


async def plan_create_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Saves the new plan to the database."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    plan_data = context.user_data['new_plan']

    success = add_plan(
        name=plan_data['name'],
        price_stars=plan_data['price'],
        price_usd=plan_data['price_usd'],
        duration_days=plan_data['duration'],
        max_accounts=plan_data['accounts'],
        daily_group_limit=plan_data['limit']
    )

    if success:
        await query.edit_message_text(_("✅ Plan '<b>{name}</b>' created successfully.").format(name=plan_data['name']), parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(_("❌ Failed to create plan '<b>{name}</b>'. It might already exist.").format(name=plan_data['name']), parse_mode=ParseMode.HTML)

    context.user_data.pop('new_plan', None)

    # Show the main plans menu again
    await plans_menu_handler(update, context)
    return ConversationHandler.END


async def plan_create_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the plan creation process."""
    query = update.callback_query
    await query.answer()
    _ = get_translation_func_for_user(update.effective_user.id)

    context.user_data.pop('new_plan', None)
    await query.edit_message_text(_("Plan creation cancelled."))

    await plans_menu_handler(update, context)
    return ConversationHandler.END


create_plan_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(plan_create_start, pattern='^admin_plan_create_start$')],
    states={
        PLAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_name)],
        PLAN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_price)],
        PLAN_PRICE_USD: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_price_usd)],
        PLAN_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_duration)],
        PLAN_ACCOUNTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_accounts)],
        PLAN_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_limit)],
        PLAN_CONFIRM: [
            CallbackQueryHandler(plan_create_save, pattern='^admin_plan_create_save$'),
        ]
    },
    fallbacks=[
        CommandHandler('cancel', conv_cancel),
        CallbackQueryHandler(plan_create_cancel, pattern='^admin_plan_create_cancel$')
        ],
    # Allow other handlers to be used while the conversation is active
    block=False,
    per_message=False,
)


async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for all admin callbacks starting with 'admin_'."""
    query = update.callback_query
    action = query.data

    if action == 'admin_menu_main':
        await admin_panel_handler(update, context)
    elif action == 'admin_view_stats':
        await stats_view_handler(update, context)
    elif action == 'admin_menu_users':
        await users_menu_handler(update, context)
    elif query.data.startswith('admin_users_list_'):
        await users_list_handler(update, context)
    elif query.data.startswith('admin_user_view_'):
        await user_view_handler(update, context)
    elif action == 'admin_menu_plans':
        await plans_menu_handler(update, context)
    elif action == 'admin_plans_list':
        await plans_list_handler(update, context)
    elif action == 'admin_plan_edit_list':
        await edit_plan_list_handler(update, context)
    elif query.data.startswith('admin_plan_edit_'):
        await edit_plan_menu_handler(update, context)
    elif query.data == 'edit_field_toggle_active':
        await edit_field_toggle_active(update, context)
    else:
        # Fallback for any unhandled admin actions
        await query.answer("This action is not yet implemented.")


# --- Handler Registration ---
# A list of Handler objects for the main application to register.
admin_handlers_list = [
    # New Admin Panel
    CommandHandler("admin", admin_panel_handler, filters=admin_filter),
    # Conversation handlers must come before the generic callback router to catch their entry points
    create_plan_conv_handler,
    grant_sub_conv_handler,
    edit_plan_conv_handler,
    # Generic callback router for menus
    CallbackQueryHandler(admin_callback_router, pattern="^admin_"),
]
