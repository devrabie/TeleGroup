import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, filters
from telegram.constants import ParseMode

from src import config
from src.database import add_plan, get_all_plans, get_all_users, get_user_details, grant_subscription
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

# --- Custom Filters ---
# In python-telegram-bot, filters are handled differently. We'll apply them when creating the CommandHandler.
admin_filter = filters.User(user_id=config.ADMIN_IDS)

# --- Command Handlers ---

async def create_plan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to create a new subscription plan."""
    _ = get_translation_func_for_user(update.effective_user.id)

    if len(context.args) != 5:
        await update.message.reply_text(
            _("<b>Usage:</b> <code>/create_plan &lt;name&gt; &lt;price&gt; &lt;days&gt; &lt;accounts&gt; &lt;limit&gt;</code>\n\n"
              "<b>Example:</b> <code>/create_plan Basic 100 30 2 10</code>"),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        name, price_str, days_str, accounts_str, limit_str = context.args
        price = int(price_str)
        days = int(days_str)
        accounts = int(accounts_str)
        limit = int(limit_str)
    except ValueError:
        await update.message.reply_text(_("❌ Invalid number format in arguments."))
        return

    if add_plan(name, price, days, accounts, limit):
        await update.message.reply_text(
            _("✅ Plan '<b>{plan_name}</b>' created successfully.").format(plan_name=name),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            _("❌ Failed to create plan '<b>{plan_name}</b>'. It might already exist or a database error occurred.").format(plan_name=name),
            parse_mode=ParseMode.HTML
        )


async def list_plans_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list all subscription plans."""
    _ = get_translation_func_for_user(update.effective_user.id)

    plans = get_all_plans(active_only=False)
    if not plans:
        await update.message.reply_text(_("No subscription plans found."))
        return

    reply = _("<b>Subscription Plans:</b>\n\n")
    for plan in plans:
        status = _("Active") if plan['is_active'] else _("Inactive")
        reply += (
            _("<b>ID:</b> <code>{id}</code>\n"
              "<b>Name:</b> {name}\n"
              "<b>Price:</b> {price} Stars\n"
              "<b>Duration:</b> {days} days\n"
              "<b>Accounts:</b> {accounts}\n"
              "<b>Daily Limit:</b> {limit} groups/day\n"
              "<b>Status:</b> {status}\n"
              "--------------------\n").format(
                id=plan['id'], name=plan['name'], price=plan['price_stars'],
                days=plan['duration_days'], accounts=plan['max_accounts'],
                limit=plan['daily_group_limit'], status=status
            )
        )

    await update.message.reply_text(reply, parse_mode=ParseMode.HTML)


async def list_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to list all users."""
    _ = get_translation_func_for_user(update.effective_user.id)

    users = get_all_users()
    if not users:
        await update.message.reply_text(_("No users found."))
        return

    reply = _("<b>Bot Users:</b>\n\n")
    for user in users:
        admin_badge = _(" (Admin)") if user['is_admin'] else ""
        reply += f"👤 <code>{user['telegram_id']}</code>{admin_badge}\n"

    await update.message.reply_text(reply, parse_mode=ParseMode.HTML)


async def view_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to view details of a specific user."""
    _ = get_translation_func_for_user(update.effective_user.id)

    if len(context.args) != 1:
        await update.message.reply_text(
            _("<b>Usage:</b> <code>/view_user &lt;telegram_id&gt;</code>"),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(_("Invalid Telegram ID."))
        return

    details = get_user_details(user_id)
    if not details:
        await update.message.reply_text(
            _("No user found with ID <code>{user_id}</code>.").format(user_id=user_id),
            parse_mode=ParseMode.HTML
        )
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

    await update.message.reply_text(reply, parse_mode=ParseMode.HTML)

async def grant_subscription_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually grant a subscription to a user."""
    _ = get_translation_func_for_user(update.effective_user.id)

    if len(context.args) != 3:
        await update.message.reply_text(
            _("<b>Usage:</b> <code>/grant_subscription &lt;telegram_id&gt; &lt;plan_id&gt; &lt;duration_days&gt;</code>"),
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(context.args[0])
        plan_id = int(context.args[1])
        days = int(context.args[2])
    except ValueError:
        await update.message.reply_text(_("Invalid number format in arguments."))
        return

    success, msg = grant_subscription(user_id, plan_id, days)
    if success:
        await update.message.reply_text(f"✅ {msg}")
    else:
        await update.message.reply_text(f"❌ {msg}")


# --- Handler Registration ---
# A list of Handler objects for the main application to register.
admin_handlers_list = [
    CommandHandler("create_plan", create_plan_handler, filters=admin_filter),
    CommandHandler("list_plans", list_plans_handler, filters=admin_filter),
    CommandHandler("list_users", list_users_handler, filters=admin_filter),
    CommandHandler("view_user", view_user_handler, filters=admin_filter),
    CommandHandler("grant_subscription", grant_subscription_handler, filters=admin_filter),
]
