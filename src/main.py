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
                    bot = request.app['ptb_app'].bot
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
    log.info("--- RUNNING JULES'S LATEST VERSION OF MAIN.PY ---")
    log.info("Initializing database...")
    initialize_database()

    log.info("Building bot application...")
    application = Application.builder().token(config.BOT_TOKEN).build()

    # --- Handler Registration ---
    all_handlers = admin_handlers_list + user_handlers_list
    application.add_handlers(all_handlers)
    log.info(f"Registered {len(all_handlers)} handlers.")

    # --- Initialize the application ---
    await application.initialize()

    # --- Webhook or Polling ---
    if config.WEBHOOK_ENABLED:
        if not all([config.WEBHOOK_URL, config.WEBHOOK_SECRET, config.CRYPTO_PAY_API_TOKEN]):
            raise ValueError("WEBHOOK_URL, WEBHOOK_SECRET, and CRYPTO_PAY_API_TOKEN must be set when WEBHOOK_ENABLED is true.")

        # --- Configure webhooks and paths ---
        bot_webhook_path = f"/{config.BOT_TOKEN.split(':')[-1]}"
        crypto_webhook_path = f"/webhooks/cryptopay/{config.CRYPTO_PAY_API_TOKEN[:10]}"
        full_bot_webhook_url = f"{config.WEBHOOK_URL.rstrip('/')}{bot_webhook_path}"

        await application.bot.set_webhook(
            url=full_bot_webhook_url,
            secret_token=config.WEBHOOK_SECRET,
            drop_pending_updates=True
        )
        log.info(f"Bot webhook set to: {full_bot_webhook_url}")
        log.warning(f"Ensure your Crypto Pay app is configured to send webhooks to: {config.WEBHOOK_URL.rstrip('/')}{crypto_webhook_path}")

        # --- Define aiohttp handlers ---
        async def telegram_handler(request: web.Request):
            """Handles incoming updates from Telegram by passing them to PTB."""
            if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
                return web.Response(status=403)
            try:
                update = Update.de_json(await request.json(), application.bot)
                await application.process_update(update)
                return web.Response()
            except (json.JSONDecodeError, TypeError):
                return web.Response(status=400, text="Bad Request")

        # --- Set up aiohttp server ---
        webapp = web.Application()
        webapp['ptb_app'] = application # Make PTB app accessible in handlers
        webapp.router.add_post(bot_webhook_path, telegram_handler)
        webapp.router.add_post(crypto_webhook_path, crypto_webhook_handler)

        runner = web.AppRunner(webapp)
        await runner.setup()
        site = web.TCPSite(runner, config.WEBHOOK_LISTEN_ADDRESS, config.WEBHOOK_PORT)

        log.info(f"Starting aiohttp server on {config.WEBHOOK_LISTEN_ADDRESS}:{config.WEBHOOK_PORT}")
        await site.start()

        # Start background jobs
        application.job_queue.run_repeating(update_proxies_from_url, interval=86400, first=10)
        application.job_queue.run_repeating(run_group_creation_cycle, interval=300, first=20)
        await application.start()
        log.info("Bot and job queue started in webhook mode.")

        # Keep the script running
        await asyncio.Event().wait()

    else:
        # --- Start in Polling Mode ---
        log.info("Starting bot in polling mode...")

        # Add jobs to the queue. They will start when application.start() is called.
        application.job_queue.run_repeating(update_proxies_from_url, interval=86400, first=10)
        application.job_queue.run_repeating(run_group_creation_cycle, interval=300, first=20)

        # Start the job queue
        await application.start()
        # Start the updater to begin polling for updates
        await application.updater.start_polling(drop_pending_updates=True)
        log.info("Bot started successfully in polling mode.")

        # Block the script until a signal is received
        await application.updater.idle()

        # Gracefully stop the bot
        log.info("Shutting down bot...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        log.info("Bot shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as e:
        log.error(f"Stopping bot due to: {e}")
