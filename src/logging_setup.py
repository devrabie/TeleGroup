"""Structured JSON logging for the bot process."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

# Shorter values are skipped so a test token like "1:test" cannot blank ordinary words.
_MIN_SECRET_LENGTH = 8


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Known secrets are replaced before the line is written."""

    def __init__(self, secrets: Sequence[str] | None = None) -> None:
        super().__init__()
        self._secrets = tuple(
            sorted(
                {item for item in (secrets or ()) if item and len(item) >= _MIN_SECRET_LENGTH},
                key=len,
                reverse=True,
            )
        )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def webhook_log_target(url: str) -> str:
    """Scheme, host, and port only.

    The webhook path is a piece of the bot token (or the Crypto Pay token) and must
    not be written to journald.
    """
    parsed = urlsplit((url or "").strip())
    host = parsed.hostname
    if not host or not parsed.scheme:
        return "webhook"
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        return f"{parsed.scheme}://{host}:{parsed.port}"
    return f"{parsed.scheme}://{host}"


def _runtime_secrets() -> list[str]:
    from src.config import get_settings

    settings = get_settings()
    secrets: list[str] = []
    for value in (
        settings.bot_token,
        settings.crypto_pay_api_token,
        settings.webhook_secret,
        settings.payment_provider_token,
    ):
        if not value:
            continue
        secrets.append(value)
        if ":" in value:
            secrets.append(value.split(":", 1)[1])
    crypto = settings.crypto_pay_api_token or ""
    if len(crypto) >= 10:
        secrets.append(crypto[:10])
    return secrets


def configure_logging(level: str = "INFO", *, secrets: Sequence[str] | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(_runtime_secrets() if secrets is None else secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
