import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from src import config
from src.database import add_plan, get_all_plans, get_all_users, get_user_details, grant_subscription
from src.translation import get_translation_func_for_user

log = logging.getLogger(__name__)

# --- Custom Filters ---

async def _is_admin_check(_, __, message: Message):
    """Custom filter to check if the user is an admin."""
    return bool(message.from_user and message.from_user.id in config.ADMIN_IDS)

is_admin = filters.create(_is_admin_check)


# --- Command Handlers ---

@filters.command("create_plan")
async def create_plan_handler(client: Client, message: Message):
    """
    Admin command to create a new subscription plan.
    Usage: /create_plan <name> <price_stars> <duration_days> <max_accounts> <daily_group_limit>
    """
    if not await is_admin.check(message):
        return
    _ = get_translation_func_for_user(message.from_user.id)

    parts = message.text.split(maxsplit=5)
    if len(parts) != 6:
        await message.reply_text(
            _("<b>Usage:</b> <code>/create_plan &lt;name&gt; &lt;price&gt; &lt;days&gt; &lt;accounts&gt; &lt;limit&gt;</code>\n\n"
              "<b>Example:</b> <code>/create_plan Basic 100 30 2 10</code>")
        )
        return

    try:
        _, name, price_str, days_str, accounts_str, limit_str = parts
        price = int(price_str)
        days = int(days_str)
        accounts = int(accounts_str)
        limit = int(limit_str)
    except ValueError:
        await message.reply_text(_("❌ Invalid number format in arguments."))
        return

    if add_plan(name, price, days, accounts, limit):
        await message.reply_text(_("✅ Plan '<b>{plan_name}</b>' created successfully.").format(plan_name=name))
    else:
        await message.reply_text(_("❌ Failed to create plan '<b>{plan_name}</b>'. It might already exist or a database error occurred.").format(plan_name=name))


@filters.command("list_plans")
async def list_plans_handler(client: Client, message: Message):
    """Admin command to list all subscription plans."""
    if not await is_admin.check(message):
        return
    _ = get_translation_func_for_user(message.from_user.id)

    plans = get_all_plans(active_only=False)
    if not plans:
        await message.reply_text(_("No subscription plans found."))
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

    await message.reply_text(reply)

# --- User Management Handlers ---

@filters.command("list_users")
async def list_users_handler(client: Client, message: Message):
    """Admin command to list all users."""
    if not await is_admin.check(message):
        return
    _ = get_translation_func_for_user(message.from_user.id)

    users = get_all_users()
    if not users:
        await message.reply_text(_("No users found."))
        return

    reply = _("<b>Bot Users:</b>\n\n")
    for user in users:
        admin_badge = _(" (Admin)") if user['is_admin'] else ""
        reply += f"👤 <code>{user['telegram_id']}</code>{admin_badge}\n"

    await message.reply_text(reply)


@filters.command("view_user")
async def view_user_handler(client: Client, message: Message):
    """Admin command to view details of a specific user."""
    if not await is_admin.check(message):
        return
    _ = get_translation_func_for_user(message.from_user.id)

    parts = message.text.split()
    if len(parts) != 2:
        await message.reply_text(_("<b>Usage:</b> <code>/view_user &lt;telegram_id&gt;</code>"))
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply_text(_("Invalid Telegram ID."))
        return

    details = get_user_details(user_id)
    if not details:
        await message.reply_text(_("No user found with ID <code>{user_id}</code>.").format(user_id=user_id))
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

    await message.reply_text(reply)

@filters.command("grant_subscription")
async def grant_subscription_handler(client: Client, message: Message):
    """Admin command to manually grant a subscription to a user."""
    if not await is_admin.check(message):
        return
    _ = get_translation_func_for_user(message.from_user.id)

    parts = message.text.split()
    if len(parts) != 4:
        await message.reply_text(_("<b>Usage:</b> <code>/grant_subscription &lt;telegram_id&gt; &lt;plan_id&gt; &lt;duration_days&gt;</code>"))
        return

    try:
        user_id = int(parts[1])
        plan_id = int(parts[2])
        days = int(parts[3])
    except ValueError:
        await message.reply_text(_("Invalid number format in arguments."))
        return

    success, msg = grant_subscription(user_id, plan_id, days)
    # The msg from DB is not translated, but it's simple english. Good enough for an admin command.
    if success:
        await message.reply_text(f"✅ {msg}")
    else:
        await message.reply_text(f"❌ {msg}")


# --- Handler Registration ---
# A list of all handlers to be registered in the main app
# The handlers themselves are decorated with filters, so we just need to list the functions
admin_handlers_list = [
    create_plan_handler,
    list_plans_handler,
    list_users_handler,
    view_user_handler,
    grant_subscription_handler,
]
