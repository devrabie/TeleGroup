import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src import config
from src.database import initialize_database, get_db_connection
from src.admin_handlers import admin_handlers_list
from src.user_handlers import user_handlers_list
from src.proxy_manager import update_proxies_from_url
from src.automation import run_group_creation_cycle

# --- Logging Setup ---
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# --- Pyrogram Client Initialization ---
# The bot client that interacts with the Telegram Bot API
app = Client(
    "bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workdir="data/"  # To store the .session file in the data directory
)


# --- Command Handlers ---

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    """
    Handles the /start command.
    Greets the user and shows a different menu for admins.
    """
    user_id = message.from_user.id
    log.info(f"/start command from user_id: {user_id}")

    # Register user in the database if not already present
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (user_id,))
            conn.commit()
            log.info(f"User {user_id} registered or already exists.")
    except Exception as e:
        log.error(f"Error registering user {user_id}: {e}")
        await message.reply_text("An error occurred. Please try again later.")
        return

    # Check if the user is an admin
    if user_id in config.ADMIN_IDS:
        # Admin welcome message and menu
        reply_text = "أهلاً بك أيها الأدمن! 👋\n\n"
        reply_text += "يمكنك استخدام لوحة تحكم الأدمن لإدارة البوت."
        # TODO: Add admin keyboard markup
    else:
        # Regular user welcome message
        reply_text = "أهلاً بك في بوت إدارة الحسابات! 👋\n\n"
        reply_text += "استخدم الأوامر المتاحة لإدارة حساباتك."
        # TODO: Add user keyboard markup

    await message.reply_text(reply_text)


# --- Main Application Logic ---

async def main():
    """
    The main entry point for the bot.
    """
    log.info("Initializing database...")
    initialize_database()

    from pyrogram.handlers import MessageHandler, CallbackQueryHandler, PreCheckoutQueryHandler

    log.info("Registering handlers...")

    # Combined list of all handlers
    all_handlers = admin_handlers_list + user_handlers_list

    for handler_func, handler_filter, handler_type in all_handlers:
        if handler_type == "message":
            app.add_handler(MessageHandler(handler_func, filters=handler_filter))
        elif handler_type == "callback":
            app.add_handler(CallbackQueryHandler(handler_func, filters=handler_filter))
        elif handler_type == "pre_checkout":
            app.add_handler(PreCheckoutQueryHandler(handler_func, filters=handler_filter))
        # Add other handler types like EditedMessageHandler if needed in the future

    log.info(f"Registered {len(all_handlers)} handlers.")

    # --- Scheduler Setup ---
    scheduler = AsyncIOScheduler()
    # Job 1: Update proxies daily
    scheduler.add_job(update_proxies_from_url, 'interval', hours=24, misfire_grace_time=3600)
    # Job 2: Run the group creation cycle every 5 minutes
    scheduler.add_job(run_group_creation_cycle, 'interval', minutes=5, misfire_grace_time=60)
    scheduler.start()

    # Run proxy update once at startup
    log.info("Performing initial proxy update on startup...")
    update_proxies_from_url()

    log.info("Starting bot client...")
    await app.start()
    log.info("Bot client started successfully.")

    # Keep the bot running indefinitely
    await asyncio.Event().wait()

    log.info("Stopping bot client...")
    await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped manually.")
    except ValueError as e:
        # This will catch the config validation errors
        log.error(f"Configuration Error: {e}")
