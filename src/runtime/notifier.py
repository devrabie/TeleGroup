"""Send owner notifications from the worker or the embedded runtime."""

from __future__ import annotations

import logging
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, telegram_id: int, text: str) -> None: ...


class BotNotifier:
    """Uses a python-telegram-bot ``Bot``. The worker does not poll updates."""

    def __init__(self, bot: Any | None = None) -> None:
        self.bot = bot

    async def send(self, telegram_id: int, text: str) -> None:
        bot = self._bot()
        from telegram.constants import ParseMode

        await bot.send_message(chat_id=telegram_id, text=text, parse_mode=ParseMode.HTML)

    def _bot(self) -> Any:
        if self.bot is not None:
            return self.bot
        from telegram import Bot

        from src.config import get_settings

        self.bot = Bot(get_settings().bot_token)
        return self.bot


class RecordingNotifier:
    """Test double that keeps every message."""

    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send(self, telegram_id: int, text: str) -> None:
        self.messages.append((telegram_id, text))
        log.info("Recorded notice for %s", telegram_id)
