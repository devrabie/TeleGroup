"""Phase 1 checks: Stars validation, settings, encryption, and Alembic."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from src.config import Settings
from src.crypto import decrypt_session, encrypt_session, is_encrypted_session
from src.logging_setup import JsonFormatter
from src.payments import (
    AMOUNT_MISMATCH,
    BAD_CURRENCY,
    INVALID_PAYLOAD,
    PLAN_INACTIVE,
    PLAN_MISSING,
    USER_MISSING,
    WRONG_USER,
    validate_stars_payment,
)
from src.tools.migrate_sqlite import migrate
from src.user_handlers import precheckout_callback, successful_payment_callback

ROOT = Path(__file__).resolve().parents[1]
_REVOKED = "uaykgtjmscislovqzscyrzsooiglcnagpsovmqjy"


def _plan(**overrides):
    plan = {
        "id": 3,
        "name": "Pro",
        "price_stars": 100,
        "is_active": 1,
        "duration_days": 30,
    }
    plan.update(overrides)
    return plan


def test_stars_payment_accepts_matching_invoice():
    result = validate_stars_payment(
        payload="plan_3_user_9",
        currency="XTR",
        total_amount=100,
        payer_telegram_id=9,
        plan=_plan(),
        user_exists=True,
    )
    assert result.ok
    assert result.plan_id == 3


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"payload": "nope"}, INVALID_PAYLOAD),
        ({"payer_telegram_id": 8}, WRONG_USER),
        ({"user_exists": False}, USER_MISSING),
        ({"plan": None}, PLAN_MISSING),
        ({"plan": _plan(is_active=0)}, PLAN_INACTIVE),
        ({"currency": "USD"}, BAD_CURRENCY),
        ({"total_amount": 1}, AMOUNT_MISMATCH),
    ],
)
def test_stars_payment_rejects_bad_invoices(kwargs, message):
    base = {
        "payload": "plan_3_user_9",
        "currency": "XTR",
        "total_amount": 100,
        "payer_telegram_id": 9,
        "plan": _plan(),
        "user_exists": True,
    }
    base.update(kwargs)
    result = validate_stars_payment(**base)
    assert not result.ok
    assert result.message == message


def test_revoked_webshare_token_is_rejected():
    with pytest.raises(ValidationError):
        Settings(
            webshare_proxy_api_url=(
                "https://proxy.webshare.io/api/v2/proxy/list/download/"
                f"{_REVOKED}/-/any/username/direct/-/"
            )
        )


def test_webshare_url_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("WEBSHARE_PROXY_API_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.webshare_proxy_api_url == ""
    assert _REVOKED not in settings.webshare_proxy_api_url


def test_admin_ids_accept_comma_separated_values():
    settings = Settings(admin_ids="4, 5")
    assert settings.admin_ids == [4, 5]


def test_session_round_trip_and_plaintext_passthrough():
    token = encrypt_session("session-string")
    assert token != "session-string"
    assert is_encrypted_session(token)
    assert decrypt_session(token) == "session-string"
    assert decrypt_session("session-string") == "session-string"
    assert encrypt_session("") == ""
    assert encrypt_session(token) == token


def test_json_log_formatter_emits_one_object():
    record = logging.LogRecord("src.test", logging.INFO, __file__, 1, "hello", (), None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "src.test"


def test_managed_account_session_is_encrypted_at_rest(tmp_path):
    import src.database as database

    db_path = tmp_path / "bot.db"
    with patch.object(database, "DB_FILE", db_path):
        database.initialize_database()

        class Owner:
            id = 111
            first_name = "Owner"
            username = "owner"

        database.update_user_details(Owner())
        profile = database.get_random_device_profile()
        assert database.add_managed_account(111, "+15550009999", "plain-session", profile["id"])
        account_id = database.get_user_details(111)["accounts"][0]["id"]
        assert database.get_account_session_string(account_id) == "plain-session"
        with database.get_db_connection() as conn:
            stored = conn.execute(
                "SELECT session_string FROM managed_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()["session_string"]
        assert stored != "plain-session"
        assert decrypt_session(stored) == "plain-session"
        assert database.encrypt_plaintext_session_rows() == 0


def test_alembic_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = "sqlite+aiosqlite:///" + (tmp_path / "alembic.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    connection = sqlite3.connect(tmp_path / "alembic.db")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    finally:
        connection.close()
    for name in (
        "users",
        "plans",
        "subscriptions",
        "proxies",
        "managed_accounts",
        "group_creation_log",
        "info_pages",
        "device_profiles",
        "account_managers",
        "sharing_tokens",
    ):
        assert name in tables


async def test_sqlite_importer_copies_rows_and_encrypts_sessions(tmp_path):
    source = tmp_path / "legacy.db"
    connection = sqlite3.connect(source)
    connection.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            telegram_id INTEGER,
            first_name TEXT,
            username TEXT,
            is_admin INTEGER,
            language_code TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO users (id, telegram_id, first_name, username, is_admin, language_code) "
        "VALUES (1, 42, 'Ada', 'ada', 0, 'en')"
    )
    connection.execute(
        """
        CREATE TABLE managed_accounts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            phone TEXT,
            session_string TEXT,
            is_active INTEGER
        )
        """
    )
    connection.execute(
        "INSERT INTO managed_accounts (id, user_id, phone, session_string, is_active) "
        "VALUES (7, 1, '+100', 'legacy-session', 0)"
    )
    connection.commit()
    connection.close()

    destination = "sqlite+aiosqlite:///" + (tmp_path / "dest.db").as_posix()
    counts = await migrate(source, destination)
    assert counts["users"] == 1
    assert counts["managed_accounts"] == 1
    copied = sqlite3.connect(tmp_path / "dest.db")
    try:
        stored = copied.execute(
            "SELECT session_string FROM managed_accounts WHERE id = 7"
        ).fetchone()[0]
    finally:
        copied.close()
    assert stored != "legacy-session"
    assert decrypt_session(stored) == "legacy-session"


def _payment_update(amount: int, payload: str = "plan_3_user_9"):
    update = MagicMock()
    update.effective_user.id = 9
    query = MagicMock()
    query.from_user.id = 9
    query.invoice_payload = payload
    query.currency = "XTR"
    query.total_amount = amount
    query.answer = AsyncMock()
    update.pre_checkout_query = query
    payment = MagicMock()
    payment.invoice_payload = payload
    payment.currency = "XTR"
    payment.total_amount = amount
    update.message.successful_payment = payment
    update.message.reply_text = AsyncMock()
    return update


async def test_precheckout_rejects_amount_mismatch():
    update = _payment_update(5)
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.get_plan_by_id", return_value=_plan()),
        patch("src.user_handlers.get_internal_user_id", return_value=1),
    ):
        await precheckout_callback(update, MagicMock())
    update.pre_checkout_query.answer.assert_awaited()
    assert update.pre_checkout_query.answer.await_args.kwargs["ok"] is False


async def test_precheckout_approves_a_valid_invoice():
    update = _payment_update(100)
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.get_plan_by_id", return_value=_plan()),
        patch("src.user_handlers.get_internal_user_id", return_value=1),
    ):
        await precheckout_callback(update, MagicMock())
    assert update.pre_checkout_query.answer.await_args.kwargs["ok"] is True


async def test_successful_payment_does_not_grant_when_amount_mismatches():
    update = _payment_update(5)
    grant = MagicMock()
    with (
        patch("src.user_handlers.get_translation_func_for_user", return_value=lambda text: text),
        patch("src.user_handlers.get_plan_by_id", return_value=_plan()),
        patch("src.user_handlers.get_internal_user_id", return_value=1),
        patch("src.user_handlers.grant_subscription", grant),
    ):
        await successful_payment_callback(update, MagicMock())
    grant.assert_not_called()
    update.message.reply_text.assert_awaited()


def test_module_settings_do_not_embed_the_revoked_proxy_token():
    from src import config

    assert config.WEBSHARE_PROXY_API_URL == "" or _REVOKED not in config.WEBSHARE_PROXY_API_URL
    assert os.environ["DATABASE_URL"].startswith("sqlite+aiosqlite://")
