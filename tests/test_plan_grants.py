"""Manual plan grants, one-time activation codes, and the grant audit log."""

from __future__ import annotations

import sqlite3
import string
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from babel.messages.pofile import read_po
from sqlalchemy import select
from src.database import _run
from src.db.engine import session_scope
from src.db.models import ActivationCode
from src.subscription_grants import (
    ACTION_EXTEND,
    ACTION_GRANT,
    ACTION_REPLACE,
    ACTION_REVOKE,
    ERR_EXPIRED,
    ERR_INVALID_BATCH,
    ERR_INVALID_CODE,
    ERR_INVALID_DURATION,
    ERR_INVALID_TARGET,
    ERR_NOT_REVOCABLE,
    ERR_REVOKED,
    ERR_USED,
    ERR_USERNAME_UNKNOWN,
    SOURCE_CODE,
    SOURCE_MANUAL,
    apply_plan_grant,
    get_activation_code,
    issue_activation_codes,
    list_activation_codes,
    list_plan_grants,
    load_grant_target,
    parse_batch_count,
    parse_duration_days,
    parse_link_expiry,
    redeem_activation_code,
    resolve_grant_target,
    revoke_activation_code,
    revoke_subscription,
    search_users,
)

ROOT = Path(__file__).resolve().parents[1]
ADMIN_ID = 1
_CODE_ALPHABET = set(string.ascii_letters + string.digits + "-_")


class _User:
    def __init__(self, user_id: int, first_name: str, username: str | None) -> None:
        self.id = user_id
        self.first_name = first_name
        self.username = username
        self.language_code = "en"


def _use_db(database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_FILE", tmp_path / "grants.db")


def _prepare(database, tmp_path, monkeypatch) -> dict[str, int]:
    _use_db(database, tmp_path, monkeypatch)
    database.initialize_database()
    assert database.add_plan("Basic", 10, 1.0, 30, 2, 5)
    assert database.add_plan("Pro", 20, 2.0, 15, 5, 10)
    plans = database.get_all_plans(active_only=False)
    return {plan["name"]: int(plan["id"]) for plan in plans}


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=None)


def _expire(code: str) -> None:
    async def _go() -> None:
        async with session_scope() as session:
            row = await session.scalar(select(ActivationCode).where(ActivationCode.code == code))
            assert row is not None
            row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)

    _run(_go())


def _active_rows(database) -> list[sqlite3.Row]:
    with database.get_db_connection() as conn:
        return list(conn.execute("SELECT plan_id, is_active FROM subscriptions").fetchall())


def test_grant_creates_missing_user_extend_replace_and_revoke(tmp_path, monkeypatch):
    import src.database as database

    plans = _prepare(database, tmp_path, monkeypatch)
    absent = resolve_grant_target("555001")
    assert absent.status == "absent"
    assert database.get_internal_user_id(555001) is None

    created = apply_plan_grant(
        telegram_id=555001,
        plan_id=plans["Basic"],
        duration_days=10,
        mode="grant",
        admin_telegram_id=ADMIN_ID,
        create_user=True,
    )
    assert created.ok
    assert created.user_created
    assert created.action == ACTION_GRANT
    assert created.plan_name == "Basic"
    first_end = _at(created.end_date or "")

    again = apply_plan_grant(
        telegram_id=555001,
        plan_id=plans["Basic"],
        duration_days=10,
        mode="grant",
        admin_telegram_id=ADMIN_ID,
        create_user=True,
    )
    assert not again.ok
    assert again.error == "choose_mode"

    extended = apply_plan_grant(
        telegram_id=555001,
        plan_id=plans["Pro"],
        duration_days=7,
        mode="extend",
        admin_telegram_id=ADMIN_ID,
    )
    assert extended.ok
    assert extended.action == ACTION_EXTEND
    assert extended.plan_name == "Pro"
    assert not extended.user_created
    extended_by = _at(extended.end_date or "") - first_end
    assert abs(extended_by - timedelta(days=7)) < timedelta(seconds=5)
    assert sum(row["is_active"] for row in _active_rows(database)) == 1

    replaced = apply_plan_grant(
        telegram_id=555001,
        plan_id=plans["Basic"],
        duration_days=5,
        mode="replace",
        admin_telegram_id=ADMIN_ID,
    )
    assert replaced.ok
    assert replaced.action == ACTION_REPLACE
    now = datetime.now(UTC).replace(tzinfo=None)
    assert abs((_at(replaced.end_date or "") - now) - timedelta(days=5)) < timedelta(seconds=5)
    rows = _active_rows(database)
    assert sum(row["is_active"] for row in rows) == 1
    assert any(row["plan_id"] == plans["Basic"] and row["is_active"] for row in rows)
    assert any(not row["is_active"] for row in rows)

    viewed = load_grant_target(555001)
    assert viewed.status == "ready"
    assert viewed.subscription is not None
    assert viewed.subscription.is_current
    assert viewed.subscription.plan_name == "Basic"

    revoked = revoke_subscription(telegram_id=555001, admin_telegram_id=ADMIN_ID)
    assert revoked.ok
    assert revoked.action == ACTION_REVOKE
    assert load_grant_target(555001).subscription is None
    assert revoke_subscription(telegram_id=555001, admin_telegram_id=ADMIN_ID).error == (
        "no_subscription"
    )

    audit = list_plan_grants(telegram_id=555001)
    assert [(row["source"], row["action"], row["duration_days"]) for row in audit] == [
        (SOURCE_MANUAL, ACTION_GRANT, 10),
        (SOURCE_MANUAL, ACTION_EXTEND, 7),
        (SOURCE_MANUAL, ACTION_REPLACE, 5),
        (SOURCE_MANUAL, ACTION_REVOKE, 0),
    ]
    assert {row["admin_telegram_id"] for row in audit} == {ADMIN_ID}
    assert [row["plan_id"] for row in audit] == [
        plans["Basic"],
        plans["Pro"],
        plans["Basic"],
        plans["Basic"],
    ]


def test_paid_grant_does_not_write_an_audit_row(tmp_path, monkeypatch):
    import src.database as database

    plans = _prepare(database, tmp_path, monkeypatch)
    database.update_user_details(_User(42, "Payer", "payer"))
    assert database.grant_subscription(42, plans["Basic"], 30)[0]
    assert list_plan_grants(telegram_id=42) == []


def test_lookup_and_search(tmp_path, monkeypatch):
    import src.database as database

    _prepare(database, tmp_path, monkeypatch)
    database.update_user_details(_User(11, "Sam One", "samone"))
    database.update_user_details(_User(12, "Sam Two", "samtwo"))
    database.update_user_details(_User(13, "Sam Three", "samthree"))

    found = resolve_grant_target("@SamOne")
    assert found.status == "ready"
    assert found.telegram_id == 11
    assert resolve_grant_target("@nobody").status == ERR_USERNAME_UNKNOWN
    assert resolve_grant_target("not an id").status == ERR_INVALID_TARGET
    assert resolve_grant_target("0").status == ERR_INVALID_TARGET

    page = search_users("sam", page=0, page_size=2)
    assert page.total == 3
    assert len(page.items) == 2
    rest = search_users("sam", page=1, page_size=2)
    assert len(rest.items) == 1
    assert {item.telegram_id for item in page.items + rest.items} == {11, 12, 13}
    assert search_users("samtwo").items[0].telegram_id == 12
    assert search_users("").total == 0


def test_duration_batch_and_expiry_parsing():
    assert parse_duration_days("30") == 30
    assert parse_duration_days("0") is None
    assert parse_duration_days("3651") is None
    assert parse_batch_count("50") == 50
    assert parse_batch_count("51") is None
    assert parse_link_expiry("2020-01-01") is None
    future = parse_link_expiry("14")
    assert future is not None
    assert future > datetime.now(UTC).replace(tzinfo=None)


def test_activation_codes_redeem_once_extend_expire_and_revoke(tmp_path, monkeypatch):
    import src.database as database

    plans = _prepare(database, tmp_path, monkeypatch)
    database.update_user_details(_User(77, "Redeemer", "redeemer"))
    seeded = apply_plan_grant(
        telegram_id=77,
        plan_id=plans["Basic"],
        duration_days=10,
        mode="grant",
        admin_telegram_id=ADMIN_ID,
    )
    assert seeded.ok

    rejected = issue_activation_codes(
        plan_id=plans["Pro"],
        duration_days=30,
        count=51,
        admin_telegram_id=ADMIN_ID,
    )
    assert rejected.error == ERR_INVALID_BATCH
    assert (
        issue_activation_codes(
            plan_id=plans["Pro"],
            duration_days=0,
            count=1,
            admin_telegram_id=ADMIN_ID,
        ).error
        == ERR_INVALID_DURATION
    )

    batch = issue_activation_codes(
        plan_id=plans["Pro"],
        duration_days=30,
        count=3,
        admin_telegram_id=ADMIN_ID,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2),
    )
    assert batch.ok
    assert len(batch.codes) == 3
    assert len({item.code for item in batch.codes}) == 3
    for item in batch.codes:
        assert item.code.startswith("act_")
        assert set(item.code) <= _CODE_ALPHABET
        assert len(item.code) <= 64

    large = issue_activation_codes(
        plan_id=plans["Basic"],
        duration_days=1,
        count=50,
        admin_telegram_id=ADMIN_ID,
    )
    assert large.ok and len(large.codes) == 50

    code = batch.codes[0].code
    redeemed = redeem_activation_code(code, 77)
    assert redeemed.ok
    assert redeemed.action == ACTION_EXTEND
    assert redeemed.plan_name == "Pro"
    assert abs((_at(redeemed.end_date or "") - _at(seeded.end_date or "")) - timedelta(days=30)) < (
        timedelta(seconds=5)
    )
    second = redeem_activation_code(code, 77)
    assert not second.ok
    assert second.error == ERR_USED
    assert sum(row["is_active"] for row in _active_rows(database)) == 1

    detail = get_activation_code(batch.codes[0].id)
    assert detail is not None
    assert detail.status == "used"
    assert detail.redeemed_telegram_id == 77
    assert detail.redeemed_username == "redeemer"
    assert list_activation_codes("used").total >= 1
    assert revoke_activation_code(code_id=batch.codes[0].id, admin_telegram_id=ADMIN_ID) == (
        ERR_NOT_REVOCABLE
    )

    unused = batch.codes[1]
    assert list_activation_codes("unused").total >= 1
    assert revoke_activation_code(code_id=unused.id, admin_telegram_id=ADMIN_ID) == "ok"
    assert redeem_activation_code(unused.code, 77).error == ERR_REVOKED
    revoked = get_activation_code(unused.id)
    assert revoked is not None and revoked.status == "revoked"
    assert list_activation_codes("revoked").total >= 1

    expiring = batch.codes[2]
    _expire(expiring.code)
    assert redeem_activation_code(expiring.code, 77).error == ERR_EXPIRED
    expired = get_activation_code(expiring.id)
    assert expired is not None
    assert expired.status == "expired"
    assert expired.used_at is None
    assert list_activation_codes("expired").total >= 1
    assert redeem_activation_code("act_not-a-real-code", 77).error == ERR_INVALID_CODE

    code_audit = [row for row in list_plan_grants(telegram_id=77) if row["source"] == SOURCE_CODE]
    assert len(code_audit) == 1
    assert code_audit[0]["admin_telegram_id"] == ADMIN_ID
    assert code_audit[0]["plan_id"] == plans["Pro"]
    assert code_audit[0]["duration_days"] == 30
    assert code_audit[0]["action"] == ACTION_EXTEND
    assert code_audit[0]["activation_code_id"] == batch.codes[0].id


def test_two_users_cannot_redeem_the_same_code(tmp_path, monkeypatch):
    import src.database as database

    plans = _prepare(database, tmp_path, monkeypatch)
    database.update_user_details(_User(201, "One", "one"))
    database.update_user_details(_User(202, "Two", "two"))
    issued = issue_activation_codes(
        plan_id=plans["Basic"],
        duration_days=12,
        count=1,
        admin_telegram_id=ADMIN_ID,
    )
    assert issued.ok
    code = issued.codes[0].code
    barrier = threading.Barrier(2)
    results = []
    guard = threading.Lock()

    def _redeem(user_id: int) -> None:
        barrier.wait()
        outcome = redeem_activation_code(code, user_id)
        with guard:
            results.append((user_id, outcome))

    threads = [threading.Thread(target=_redeem, args=(user_id,)) for user_id in (201, 202)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [user_id for user_id, outcome in results if outcome.ok]
    losers = [outcome for _user_id, outcome in results if not outcome.ok]
    assert winners == [winners[0]]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].error == ERR_USED
    detail = get_activation_code(issued.codes[0].id)
    assert detail is not None
    assert detail.redeemed_telegram_id == winners[0]
    assert list_activation_codes("used").total == 1
    assert len([row for row in list_plan_grants() if row["source"] == SOURCE_CODE]) == 1


def test_migration_0005_is_reversible_after_phase4(tmp_path, monkeypatch):
    url = "sqlite+aiosqlite:///" + (tmp_path / "alembic.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "0005_plan_grants")
    command.downgrade(cfg, "0004_phase4")
    connection = sqlite3.connect(tmp_path / "alembic.db")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()
    assert version == "0004_phase4"
    assert "users" in tables
    assert "subscriptions" in tables
    assert "activation_codes" not in tables
    assert "plan_grants" not in tables

    command.upgrade(cfg, "0005_plan_grants")
    connection = sqlite3.connect(tmp_path / "alembic.db")
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(activation_codes)")}
        grant_columns = {row[1] for row in connection.execute("PRAGMA table_info(plan_grants)")}
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()
    assert version == "0005_plan_grants"
    assert {
        "code",
        "plan_id",
        "duration_days",
        "created_by_telegram_id",
        "expires_at",
        "used_at",
        "used_by_user_id",
        "revoked_at",
    } <= columns
    assert {
        "admin_telegram_id",
        "target_user_id",
        "plan_id",
        "duration_days",
        "source",
        "action",
        "activation_code_id",
    } <= grant_columns


def test_arabic_catalog_translates_grant_and_code_messages():
    with (ROOT / "locales" / "ar" / "LC_MESSAGES" / "base.po").open("rb") as handle:
        catalog = read_po(handle)
    required = [
        "This activation code is not valid.",
        "This activation code has already been used.",
        "This activation code has expired.",
        "This activation code has been revoked.",
        "✅ The <b>{plan}</b> plan is active until <b>{expiry}</b>.",
        "🎁 Grant a Plan",
        "🔗 Activation Links",
        "🛑 End Subscription",
    ]
    for msgid in required:
        message = catalog.get(msgid)
        assert message is not None, msgid
        assert message.string, msgid


def test_admin_handler_list_includes_grant_flows():
    from src.admin_grants import codes_conv_handler, grant_conv_handler
    from src.admin_handlers import admin_handlers_list

    assert grant_conv_handler in admin_handlers_list
    assert codes_conv_handler in admin_handlers_list


@pytest.mark.parametrize(
    ("mode", "days"),
    [("grant", 0), ("extend", -1)],
)
def test_invalid_duration_does_not_grant(tmp_path, monkeypatch, mode, days):
    import src.database as database

    plans = _prepare(database, tmp_path, monkeypatch)
    outcome = apply_plan_grant(
        telegram_id=8,
        plan_id=plans["Basic"],
        duration_days=days,
        mode=mode,
        admin_telegram_id=ADMIN_ID,
        create_user=True,
    )
    assert outcome.error == ERR_INVALID_DURATION
    assert list_plan_grants() == []
