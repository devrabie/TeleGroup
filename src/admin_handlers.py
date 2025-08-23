import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from src import config
from src.database import add_plan, get_all_plans

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
    Usage: /create_plan <name> <price_stars> <max_accounts> <daily_group_limit>
    """
    if not await is_admin.check(message):
        return  # Silently ignore non-admins

    parts = message.text.split(maxsplit=4)
    if len(parts) != 5:
        await message.reply_text(
            "<b>Usage:</b> <code>/create_plan &lt;name&gt; &lt;price_stars&gt; &lt;max_accounts&gt; &lt;daily_group_limit&gt;</code>\n\n"
            "<b>Example:</b> <code>/create_plan Basic 100 2 10</code>"
        )
        return

    try:
        _, name, price_str, accounts_str, limit_str = parts
        price = int(price_str)
        accounts = int(accounts_str)
        limit = int(limit_str)
    except ValueError:
        await message.reply_text("❌ Invalid number format in arguments.")
        return

    if add_plan(name, price, accounts, limit):
        await message.reply_text(f"✅ Plan '<b>{name}</b>' created successfully.")
    else:
        await message.reply_text(f"❌ Failed to create plan '<b>{name}</b>'. It might already exist or a database error occurred.")


@filters.command("list_plans")
async def list_plans_handler(client: Client, message: Message):
    """Admin command to list all subscription plans."""
    if not await is_admin.check(message):
        return

    plans = get_all_plans(active_only=False)
    if not plans:
        await message.reply_text("No subscription plans found.")
        return

    reply = "<b>Subscription Plans:</b>\n\n"
    for plan in plans:
        status = "Active" if plan['is_active'] else "Inactive"
        reply += (
            f"<b>ID:</b> <code>{plan['id']}</code>\n"
            f"<b>Name:</b> {plan['name']}\n"
            f"<b>Price:</b> {plan['price_stars']} Stars\n"
            f"<b>Accounts:</b> {plan['max_accounts']}\n"
            f"<b>Daily Limit:</b> {plan['daily_group_limit']} groups/day\n"
            f"<b>Status:</b> {status}\n"
            "--------------------\n"
        )

    await message.reply_text(reply)

# --- User Management Handlers ---

@filters.command("list_users")
async def list_users_handler(client: Client, message: Message):
    """Admin command to list all users."""
    if not await is_admin.check(message):
        return

    users = get_all_users()
    if not users:
        await message.reply_text("No users found.")
        return

    reply = "<b>Bot Users:</b>\n\n"
    for user in users:
        admin_badge = " (Admin)" if user['is_admin'] else ""
        reply += f"👤 <code>{user['telegram_id']}</code>{admin_badge}\n"

    await message.reply_text(reply)


@filters.command("view_user")
async def view_user_handler(client: Client, message: Message):
    """Admin command to view details of a specific user."""
    if not await is_admin.check(message):
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.reply_text("<b>Usage:</b> <code>/view_user &lt;telegram_id&gt;</code>")
        return

    try:
        user_id = int(parts[1])
    except ValueError:
        await message.reply_text("Invalid Telegram ID.")
        return

    details = get_user_details(user_id)
    if not details:
        await message.reply_text(f"No user found with ID <code>{user_id}</code>.")
        return

    user = details['user']
    sub = details['subscription']
    accounts = details['accounts']

    reply = f"<b>User Details for:</b> <code>{user['telegram_id']}</code>\n"
    reply += f"<b>Admin:</b> {'Yes' if user['is_admin'] else 'No'}\n"
    reply += f"<b>Joined:</b> {user['created_at']}\n"
    reply += "--------------------\n"

    if sub:
        reply += (
            f"<b>Subscription:</b> {sub['plan_name']} (Plan ID: {sub['plan_id']})\n"
            f"<b>Expires:</b> {sub['end_date']}\n"
        )
    else:
        reply += "<b>Subscription:</b> None\n"

    reply += "--------------------\n"
    reply += f"<b>Managed Accounts ({len(accounts)}):</b>\n"
    if accounts:
        for acc in accounts:
            status = "Active" if acc['is_active'] else "Inactive"
            reply += f"  - <code>{acc['phone']}</code> ({status})\n"
    else:
        reply += "  None\n"

    await message.reply_text(reply)

@filters.command("grant_subscription")
async def grant_subscription_handler(client: Client, message: Message):
    """Admin command to manually grant a subscription to a user."""
    if not await is_admin.check(message):
        return

    parts = message.text.split()
    if len(parts) != 4:
        await message.reply_text("<b>Usage:</b> <code>/grant_subscription &lt;telegram_id&gt; &lt;plan_id&gt; &lt;duration_days&gt;</code>")
        return

    try:
        user_id = int(parts[1])
        plan_id = int(parts[2])
        days = int(parts[3])
    except ValueError:
        await message.reply_text("Invalid number format in arguments.")
        return

    success, msg = grant_subscription(user_id, plan_id, days)
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
