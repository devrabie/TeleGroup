import logging
import asyncio
import json
import hashlib
import hmac

from telegram.ext import Application
from aiohttp import web

from src import config
from src.database import initialize_database, get_plan_by_id, grant_subscription
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
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

log = logging.getLogger(__name__)


async def crypto_webhook_handler(request: web.Request):
    """Handles incoming webhooks from Crypto Pay."""
    try:
        signature = request.headers.get("Crypto-Pay-API-Signature")
        if not signature:
            return web.Response(status=400, text="Signature header missing.")

        body = await request.text()

        # Verify the signature
        secret = hashlib.sha256(config.CRYPTO_PAY_API_TOKEN.encode()).digest()
        computed_hmac = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hmac, signature):
            log.warning("Invalid Crypto Pay webhook signature received.")
            return web.Response(status=403, text="Invalid signature.")

        data = json.loads(body)
        log.info(f"Received valid Crypto Pay webhook: {data}")

        if data.get("update_type") == "invoice_paid":
            invoice = data.get("payload")
            if not invoice:
                log.error("No invoice in webhook payload.")
                return web.Response(status=400)

            custom_payload = invoice.get("payload")
            if not custom_payload:
                log.error("No custom payload in invoice.")
                return web.Response(status=400)

            try:
                parts = custom_payload.split("_")
                plan_id = int(parts[1])
                user_id = int(parts[3])

                plan = get_plan_by_id(plan_id)
                if not plan:
                    log.error(f"Webhook received for non-existent plan_id: {plan_id}")
                    return web.Response(status=400)

                duration = plan['duration_days']
                success, msg = grant_subscription(user_id, plan_id, duration)

                if success:
                    log.info(f"Subscription granted via crypto webhook for user {user_id}, plan {plan_id}.")
                    bot = request.app['bot']
                    await bot.send_message(user_id, f"✅ Your payment was successful! Your '{plan['name']}' subscription is now active for {duration} days.")
                else:
                    log.error(f"Failed to grant subscription via crypto webhook for user {user_id}: {msg}")

            except (IndexError, ValueError) as e:
                log.error(f"Error parsing crypto webhook payload '{custom_payload}': {e}")
                return web.Response(status=400)

        return web.Response(status=200)

    except Exception as e:
        log.error(f"Error processing crypto webhook: {e}", exc_info=True)
        return web.Response(status=500)


async def main() -> None:
    """
    The main entry point for the bot.
    """
    log.info("Initializing database...")
    initialize_database()

    log.info("Building bot application...")
    application = Application.builder().token(config.BOT_TOKEN).build()

    # --- Scheduler Setup ---
    job_queue = application.job_queue
    job_queue.run_repeating(update_proxies_from_url, interval=86400, first=10) # Daily
    job_queue.run_repeating(run_group_creation_cycle, interval=300, first=20) # Every 5 mins
    log.info("Scheduled background jobs.")

    # --- Handler Registration ---
    all_handlers = admin_handlers_list + user_handlers_list
    application.add_handlers(all_handlers)
    log.info(f"Registered {len(all_handlers)} handlers.")

    # --- Webhook or Polling ---
    if config.WEBHOOK_ENABLED:
        if not all([config.WEBHOOK_URL, config.WEBHOOK_SECRET, config.CRYPTO_PAY_API_TOKEN]):
            raise ValueError("WEBHOOK_URL, WEBHOOK_SECRET, and CRYPTO_PAY_API_TOKEN must be set when WEBHOOK_ENABLED is true.")

        # The URL path for the bot's webhook
        bot_webhook_path = f"/{config.BOT_TOKEN.split(':')[-1]}"
        # The URL path for the Crypto Pay webhook
        crypto_webhook_path = f"/webhooks/cryptopay/{config.CRYPTO_PAY_API_TOKEN[:10]}"

        log.info(f"Starting bot in webhook mode. URL: {config.WEBHOOK_URL}, Port: {config.WEBHOOK_PORT}")
        log.info(f"Bot webhook path: {bot_webhook_path}")
        log.info(f"Crypto Pay webhook path: {crypto_webhook_path}")
        log.warning("Ensure your Crypto Pay app is configured to send webhooks to: "
                    f"{config.WEBHOOK_URL.rstrip('/')}{crypto_webhook_path}")

        # Set up the web server for webhooks
        webapp = web.Application()
        webapp['bot'] = application.bot # Make bot object accessible in handlers
        webapp.router.add_post(crypto_webhook_path, crypto_webhook_handler)

        runner = web.AppRunner(webapp)
        await runner.setup()
        site = web.TCPSite(runner, config.WEBHOOK_LISTEN_ADDRESS, config.WEBHOOK_PORT)
        await site.start()

        # Start the bot application
        await application.run_webhook(
            listen=config.WEBHOOK_LISTEN_ADDRESS,
            port=config.WEBHOOK_PORT,
            url_path=bot_webhook_path,
            webhook_url=f"{config.WEBHOOK_URL.rstrip('/')}{bot_webhook_path}",
            secret_token=config.WEBHOOK_SECRET,
            drop_pending_updates=True
        )

        # Keep the script running
        await asyncio.Event().wait()

    else:
        log.info("Starting bot in polling mode...")
        await application.initialize() # Inits bot, etc.
        await application.updater.start_polling(drop_pending_updates=True)
        await application.start()
        log.info("Bot started.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as e:
        log.error(f"Stopping bot due to: {e}")
