import logging
import asyncio

from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, PreCheckoutQueryHandler

from src import config
from src.database import initialize_database
from src.admin_handlers import admin_handlers_list
from src.user_handlers import user_handlers_list
from src.proxy_manager import update_proxies_from_url
from src.automation import run_group_creation_cycle

# --- Logging Setup ---
logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

# --- Main Application Logic ---

def main() -> None:
    """
    The main entry point for the bot.
    """
    log.info("Initializing database...")
    initialize_database()

    log.info("Building bot application...")
    application = Application.builder().token(config.BOT_TOKEN).build()

    # --- Scheduler Setup ---
    job_queue = application.job_queue
    job_queue.run_repeating(update_proxies_from_url, interval=86400, first=10) # Daily, start after 10s
    job_queue.run_repeating(run_group_creation_cycle, interval=300, first=20) # Every 5 mins, start after 20s
    log.info("Scheduled background jobs.")

    # --- Handler Registration ---
    all_handlers = admin_handlers_list + user_handlers_list
    application.add_handlers(all_handlers)
    log.info(f"Registered {len(all_handlers)} handlers.")

    log.info("Starting bot polling...")
    application.run_polling(drop_pending_updates=True)
    log.info("Bot stopped.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        # This will catch the config validation errors
        log.error(f"Configuration Error: {e}")
