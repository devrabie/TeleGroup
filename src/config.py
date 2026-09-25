"""Typed application settings loaded from the environment."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_REVOKED_WEBSHARE_TOKEN = "uaykgtjmscislovqzscyrzsooiglcnagpsovmqjy"


def _parse_admin_ids(value: object) -> list[int]:
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("ADMIN_IDS is required")
        return [int(part) for part in parts]
    raise ValueError("ADMIN_IDS must be a comma-separated list of integers")


class Settings(BaseSettings):
    """Runtime configuration. Secrets come from the environment, never from defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    api_id: int
    api_hash: str
    admin_ids: Annotated[list[int], NoDecode]
    database_url: str
    session_encryption_key: str

    payment_provider_token: str | None = None
    crypto_pay_api_token: str | None = None
    crypto_pay_api_base_url: str = "https://pay.crypt.bot/api/"

    webhook_enabled: bool = False
    webhook_url: str | None = None
    webhook_secret: str | None = None
    webhook_listen_address: str = "0.0.0.0"
    webhook_port: int = 8443

    webshare_proxy_api_url: str = ""
    proxy_username: str | None = None
    proxy_password: str | None = None
    data_proxies_file: str = "data/proxies.txt"

    display_timezone: str = "UTC"
    redis_url: str | None = None
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def split_admin_ids(cls, value: object) -> list[int]:
        return _parse_admin_ids(value)

    @field_validator("admin_ids")
    @classmethod
    def admin_ids_required(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("ADMIN_IDS is required")
        return value

    @field_validator("database_url")
    @classmethod
    def database_url_scheme(cls, value: str) -> str:
        if value.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite:///")):
            return value
        raise ValueError(
            "DATABASE_URL must start with postgresql+asyncpg:// or sqlite+aiosqlite:///"
        )

    @field_validator("session_encryption_key")
    @classmethod
    def session_key_is_fernet(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except Exception as exc:
            raise ValueError("SESSION_ENCRYPTION_KEY must be a urlsafe Fernet key") from exc
        return value

    @field_validator("webshare_proxy_api_url")
    @classmethod
    def webshare_url_has_no_revoked_token(cls, value: str) -> str:
        if _REVOKED_WEBSHARE_TOKEN in value:
            raise ValueError(
                "WEBSHARE_PROXY_API_URL contains a revoked token. "
                "Set a new URL in the environment or leave it empty to skip proxy download."
            )
        return value

    @field_validator("log_level")
    @classmethod
    def log_level_name(cls, value: str) -> str:
        name = value.upper()
        if name not in logging.getLevelNamesMapping():
            raise ValueError(f"Invalid LOG_LEVEL: {value}")
        return name

    @field_validator("redis_url", "payment_provider_token", "crypto_pay_api_token", mode="before")
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "webhook_url", "webhook_secret", "proxy_username", "proxy_password", mode="before"
    )
    @classmethod
    def empty_optional_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()

BOT_TOKEN = settings.bot_token
API_ID = settings.api_id
API_HASH = settings.api_hash
ADMIN_IDS = settings.admin_ids
PAYMENT_PROVIDER_TOKEN = settings.payment_provider_token
CRYPTO_PAY_API_TOKEN = settings.crypto_pay_api_token
CRYPTO_PAY_API_BASE_URL = settings.crypto_pay_api_base_url
WEBHOOK_ENABLED = settings.webhook_enabled
WEBHOOK_URL = settings.webhook_url
WEBHOOK_SECRET = settings.webhook_secret
WEBHOOK_LISTEN_ADDRESS = settings.webhook_listen_address
WEBHOOK_PORT = settings.webhook_port
WEBSHARE_PROXY_API_URL = settings.webshare_proxy_api_url
PROXY_USERNAME = settings.proxy_username
PROXY_PASSWORD = settings.proxy_password
DATA_PROXIES_FILE = settings.data_proxies_file
DISPLAY_TIMEZONE = settings.display_timezone
REDIS_URL = settings.redis_url
LOG_LEVEL = settings.log_level
DATABASE_URL = settings.database_url
SESSION_ENCRYPTION_KEY = settings.session_encryption_key
