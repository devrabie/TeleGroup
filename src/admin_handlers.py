import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, filters, CallbackQueryHandler, ConversationHandler, MessageHandler
from telegram.constants import ParseMode

from src import config
from src.database import add_plan, get_all_plans, get_all_users, get_user_details, grant_subscription, get_system_stats
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
                    f"👤 {user['telegram_id']}{admin_badge}",
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

    reply = _("<b>User Details for:</b> <code>{user_id}</code>\n").format(user_id=user['telegram_id'])
    is_admin_text = _("Yes") if user['is_admin'] else _("No")
    reply += _("<b>Admin:</b> {is_admin}\n").format(is_admin=is_admin_text)
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

        success, msg = grant_subscription(user_id, plan_id, duration)

        if success:
            await update.message.reply_text(f"✅ {msg}")
        else:
            await update.message.reply_text(f"❌ {msg}")

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
            CallbackQueryHandler(grant_sub_cancel, pattern='^admin_grant_cancel$')
        ],
        GRANT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_sub_receive_duration)],
    },
    fallbacks=[
        CommandHandler('cancel', conv_cancel),
        CallbackQueryHandler(grant_sub_cancel, pattern='^admin_grant_cancel$')
    ],
    block=False,
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
            text += (
                _("<b>ID:</b> <code>{id}</code>, <b>Name:</b> {name}\n"
                  "<b>Price:</b> {price} Stars, <b>Duration:</b> {days} days\n"
                  "<b>Accounts:</b> {accounts}, <b>Limit:</b> {limit} groups/day\n"
                  "<b>Status:</b> {status}\n"
                  "--------------------\n").format(
                    id=plan['id'], name=plan['name'], price=plan['price_stars'],
                    days=plan['duration_days'], accounts=plan['max_accounts'],
                    limit=plan['daily_group_limit'], status=status
                )
            )

    keyboard = [[InlineKeyboardButton(_("🔙 Back"), callback_data='admin_menu_plans')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


# --- Create Plan Conversation Handlers ---

(PLAN_NAME, PLAN_PRICE, PLAN_DURATION, PLAN_ACCOUNTS, PLAN_LIMIT, PLAN_CONFIRM) = range(10, 16)


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
    """Receives the price and asks for the duration."""
    _ = get_translation_func_for_user(update.effective_user.id)
    try:
        price = int(update.message.text)
        context.user_data['new_plan']['price'] = price
        await update.message.reply_text(_("Perfect. How many days will the subscription last? (e.g., 30)"))
        return PLAN_DURATION
    except ValueError:
        await update.message.reply_text(_("That's not a valid number. Please enter the price in Stars again."))
        return PLAN_PRICE


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
            "<b>Price:</b> {price} Stars\n"
            "<b>Duration:</b> {duration} days\n"
            "<b>Max Accounts:</b> {accounts}\n"
            "<b>Daily Limit:</b> {limit} groups/day"
        ).format(
            name=plan_data['name'],
            price=plan_data['price'],
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


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A generic cancellation command for conversations."""
    _ = get_translation_func_for_user(update.effective_user.id)

    # Clean up any potential data stored in user_data
    context.user_data.pop('new_plan', None)

    await update.message.reply_text(_("Operation cancelled."))

    # We don't know which menu to return to, so we just end.
    # A more advanced setup could store the "return menu" in user_data.
    return ConversationHandler.END


create_plan_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(plan_create_start, pattern='^admin_plan_create_start$')],
    states={
        PLAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_name)],
        PLAN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_price)],
        PLAN_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_duration)],
        PLAN_ACCOUNTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_accounts)],
        PLAN_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, plan_create_receive_limit)],
        PLAN_CONFIRM: [
            CallbackQueryHandler(plan_create_save, pattern='^admin_plan_create_save$'),
            CallbackQueryHandler(plan_create_cancel, pattern='^admin_plan_create_cancel$')
        ]
    },
    fallbacks=[CommandHandler('cancel', conv_cancel)],
    # Allow other handlers to be used while the conversation is active
    block=False
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
    else:
        # Fallback for any unhandled admin actions
        await query.answer("This action is not yet implemented.")


# --- Handler Registration ---
# A list of Handler objects for the main application to register.
admin_handlers_list = [
    # New Admin Panel
    CommandHandler("admin", admin_panel_handler, filters=admin_filter),
    CallbackQueryHandler(admin_callback_router, pattern="^admin_"),
    create_plan_conv_handler,
    grant_sub_conv_handler,
]
