"""Account runtime process.

Run with ``python -m src.worker``. Docker Compose starts this beside the bot
so each managed account has one long-lived Kurigram client. The bot wakes the
worker through Postgres NOTIFY and a short database poll.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from telegram import Bot

from src.code_monitor import code_monitor_manager
from src.config import get_settings
from src.database import initialize_database
from src.db.engine import dispose_engine
from src.logging_setup import configure_logging
from src.runtime.notifier import BotNotifier
from src.runtime.supervisor import Supervisor
from src.translation import compile_translations

log = logging.getLogger(__name__)


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info(
        "Starting account worker shard %s/%s",
        settings.worker_shard_id,
        settings.worker_shard_count,
    )
    initialize_database()
    compiled = compile_translations()
    log.info("Translation catalogs compiled/updated: %s", compiled)
    bot = Bot(settings.bot_token)
    code_monitor_manager.set_bot(bot)
    supervisor = Supervisor(notifier=BotNotifier(bot))
    await supervisor.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await stop_event.wait()
    log.info("Stopping account worker...")
    await supervisor.stop()
    await code_monitor_manager.stop_all()
    dispose_engine()
    log.info("Account worker stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
