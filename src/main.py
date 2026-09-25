import asyncio
import hashlib
import hmac
import json
import logging
import signal

from aiohttp import web
from telegram import Update
from telegram.ext import Application

from src import config
from src.admin_handlers import admin_handlers_list
from src.automation import run_group_creation_cycle
from src.code_monitor import code_monitor_manager, run_code_monitor_sync
from src.database import (
    get_plan_by_id,
    grant_subscription,
    initialize_database,
    warn_if_no_working_proxies,
)
from src.db.engine import dispose_engine
from src.logging_setup import configure_logging
from src.proxy_manager import update_proxies_from_url
from src.translation import compile_translations
from src.user_handlers import user_handlers_list

configure_logging(config.LOG_LEVEL)
log = logging.getLogger(__name__)


async def crypto_webhook_handler(request: web.Request):
    """Handles incoming webhooks from Crypto Pay."""
    try:
        signature = request.headers.get("Crypto-Pay-API-Signature")
        if not signature:
            return web.Response(status=400, text="Signature header missing.")

        body = await request.text()

        # Verify the signature
        token = config.CRYPTO_PAY_API_TOKEN
        if not token:
            return web.Response(status=503, text="Crypto Pay is not configured.")
        secret = hashlib.sha256(token.encode()).digest()
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

                duration = plan["duration_days"]
                success, msg = grant_subscription(user_id, plan_id, duration)

                if success:
                    log.info(
                        "Subscription granted via crypto webhook for user %s, plan %s.",
                        user_id,
                        plan_id,
                    )
                    bot = request.app["ptb_app"].bot
                    await bot.send_message(
                        user_id,
                        (
                            f"✅ Your payment was successful! Your '{plan['name']}' "
                            f"subscription is now active for {duration} days."
                        ),
                    )
                else:
                    log.error(
                        f"Failed to grant subscription via crypto webhook for user {user_id}: {msg}"
                    )

            except (IndexError, ValueError) as e:
                log.error(f"Error parsing crypto webhook payload '{custom_payload}': {e}")
                return web.Response(status=400)

        return web.Response(status=200)

    except Exception as e:
        log.error(f"Error processing crypto webhook: {e}", exc_info=True)
        return web.Response(status=500)


def _schedule_jobs(application: Application) -> None:
    job_queue = application.job_queue
    if job_queue is None:
        raise RuntimeError("Job queue is not available. Install python-telegram-bot[job-queue].")
    job_queue.run_repeating(update_proxies_from_url, interval=86400, first=10)
    job_queue.run_repeating(run_group_creation_cycle, interval=300, first=20)
    job_queue.run_repeating(run_code_monitor_sync, interval=60, first=25)


async def _shutdown(application: Application, runner: web.AppRunner | None) -> None:
    """Stop monitors, the bot, the webhook server, and the database engine."""
    log.info("Shutting down bot...")
    try:
        await code_monitor_manager.stop_all()
    except Exception:
        log.exception("Failed to stop code monitor clients")
    updater = getattr(application, "updater", None)
    try:
        if updater is not None and getattr(updater, "running", False):
            await updater.stop()
    except Exception:
        log.exception("Failed to stop the updater")
    try:
        if getattr(application, "running", False):
            await application.stop()
        await application.shutdown()
    except Exception:
        log.exception("Failed to stop the application")
    if runner is not None:
        await runner.cleanup()
    dispose_engine()
    log.info("Bot shut down gracefully.")


async def main() -> None:
    """
    The main entry point for the bot.
    """
    log.info("--- RUNNING JULES'S LATEST VERSION OF MAIN.PY ---")
    log.info("Initializing database...")
    initialize_database()
    warn_if_no_working_proxies()
    compiled = compile_translations()
    log.info(f"Translation catalogs compiled/updated: {compiled}")

    log.info("Building bot application with concurrent updates enabled...")
    application = Application.builder().token(config.BOT_TOKEN).concurrent_updates(16).build()

    # --- Handler Registration ---
    all_handlers = admin_handlers_list + user_handlers_list
    application.add_handlers(all_handlers)
    log.info(f"Registered {len(all_handlers)} handlers.")

    # --- Initialize the application ---
    await application.initialize()
    runner: web.AppRunner | None = None
    try:
        runner = await _serve(application)
    finally:
        await _shutdown(application, runner)


async def _serve(application: Application) -> web.AppRunner | None:
    # --- Webhook or Polling ---
    if config.WEBHOOK_ENABLED:
        webhook_url = config.WEBHOOK_URL or ""
        webhook_secret = config.WEBHOOK_SECRET or ""
        crypto_token = config.CRYPTO_PAY_API_TOKEN or ""
        if not all([webhook_url, webhook_secret, crypto_token]):
            raise ValueError(
                "WEBHOOK_URL, WEBHOOK_SECRET, and CRYPTO_PAY_API_TOKEN must be set "
                "when WEBHOOK_ENABLED is true."
            )

        # --- Configure webhooks and paths ---
        bot_webhook_path = f"/{config.BOT_TOKEN.split(':')[-1]}"
        crypto_webhook_path = f"/webhooks/cryptopay/{crypto_token[:10]}"
        full_bot_webhook_url = f"{webhook_url.rstrip('/')}{bot_webhook_path}"

        await application.bot.set_webhook(
            url=full_bot_webhook_url,
            secret_token=webhook_secret,
            drop_pending_updates=True,
        )
        log.info("Bot webhook set to: %s", full_bot_webhook_url)
        log.warning(
            "Ensure your Crypto Pay app is configured to send webhooks to: %s%s",
            webhook_url.rstrip("/"),
            crypto_webhook_path,
        )

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
        webapp["ptb_app"] = application  # Make PTB app accessible in handlers
        webapp.router.add_post(bot_webhook_path, telegram_handler)
        webapp.router.add_post(crypto_webhook_path, crypto_webhook_handler)

        runner = web.AppRunner(webapp)
        await runner.setup()
        site = web.TCPSite(runner, config.WEBHOOK_LISTEN_ADDRESS, config.WEBHOOK_PORT)

        log.info(
            f"Starting aiohttp server on {config.WEBHOOK_LISTEN_ADDRESS}:{config.WEBHOOK_PORT}"
        )
        await site.start()

        # Start background jobs
        _schedule_jobs(application)
        await application.start()
        log.info("Bot and job queue started in webhook mode.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        return runner

    else:
        # --- Start in Polling Mode ---
        log.info("Starting bot in polling mode...")

        # Add jobs to the queue. They will start when application.start() is called.
        _schedule_jobs(application)

        # Start the job queue
        await application.start()
        updater = application.updater
        if updater is None:
            raise RuntimeError("Polling updater is not available")
        await updater.start_polling(drop_pending_updates=True)
        log.info("Bot started successfully in polling mode.")

        # Block the script until a signal is received
        idle = getattr(updater, "idle", None)
        if idle is None:
            raise RuntimeError("Polling updater cannot idle")
        await idle()
        return None


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as e:
        log.error(f"Stopping bot due to: {e}")
