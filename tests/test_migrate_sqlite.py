"""SQLite importer failure reporting, plugin grants, and Alembic dotenv loading."""

from __future__ import annotations

import logging
import sqlite3

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.config import get_settings, resolve_database_url
from src.crypto import decrypt_session
from src.db.models import Base, Plan, PlanPlugin
from src.runtime.builtin_plugins import DEFAULT_PLAN_PLUGINS
from src.tools.migrate_sqlite import main, migrate

_FERNET = "qoaGk05XJfuMlnrnND1-suk6JtqXR-Y04CWEJNH5KgU="


def _url(path) -> str:
    return "sqlite+aiosqlite:///" + path.as_posix()


def _write_source(path, *, users=None, plans=None, accounts=None) -> None:
    connection = sqlite3.connect(path)
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
    for row in users or []:
        connection.execute(
            "INSERT INTO users (id, telegram_id, first_name, username, is_admin, language_code) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    if plans is not None:
        connection.execute(
            """
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY,
                name TEXT,
                price_stars INTEGER,
                price_usd REAL,
                duration_days INTEGER,
                max_accounts INTEGER,
                daily_group_limit INTEGER,
                is_active INTEGER
            )
            """
        )
        for row in plans:
            connection.execute(
                "INSERT INTO plans "
                "(id, name, price_stars, price_usd, duration_days, max_accounts, "
                "daily_group_limit, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
    if accounts is not None:
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
        for row in accounts:
            connection.execute(
                "INSERT INTO managed_accounts "
                "(id, user_id, phone, session_string, is_active) VALUES (?, ?, ?, ?, ?)",
                row,
            )
    connection.commit()
    connection.close()


async def _plugins(destination: str) -> dict[int, set[str]]:
    engine = create_async_engine(destination)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    found: dict[int, set[str]] = {}
    async with maker() as session:
        rows = (await session.execute(select(PlanPlugin.plan_id, PlanPlugin.plugin_name))).all()
    await engine.dispose()
    for plan_id, name in rows:
        found.setdefault(plan_id, set()).add(name)
    return found


async def test_importer_grants_plugins_only_to_empty_plans(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(
        source,
        plans=[(9, "Fresh", 10, 1.0, 30, 1, 1, 1)],
    )
    destination = _url(tmp_path / "dest.db")
    engine = create_async_engine(destination)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            Plan(
                id=5,
                name="Kept",
                price_stars=1,
                price_usd=1,
                duration_days=30,
                max_accounts=1,
                daily_group_limit=1,
                is_active=True,
            )
        )
        session.add(PlanPlugin(plan_id=5, plugin_name="ping"))
        await session.commit()
    await engine.dispose()

    report = await migrate(source, destination)
    assert report.failures == []
    plugins = await _plugins(destination)
    assert plugins[9] == set(DEFAULT_PLAN_PLUGINS)
    assert plugins[5] == {"ping"}
    assert report.plugins_granted == len(DEFAULT_PLAN_PLUGINS)


async def test_second_run_skips_existing_rows(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(source, users=[(1, 42, "Ada", "ada", 0, "en")])
    destination = _url(tmp_path / "dest.db")
    first = await migrate(source, destination)
    assert first["users"] == 1
    second = await migrate(source, destination)
    assert second["users"] == 0
    assert second.skipped["users"] == 1
    assert second.failures == []


async def test_upsert_updates_existing_rows(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(source, users=[(1, 42, "Ada", "ada", 0, "en")])
    destination = _url(tmp_path / "dest.db")
    await migrate(source, destination)
    connection = sqlite3.connect(source)
    connection.execute("UPDATE users SET first_name = 'Grace' WHERE telegram_id = 42")
    connection.commit()
    connection.close()
    report = await migrate(source, destination, mode="upsert")
    assert report.failures == []
    copied = sqlite3.connect(tmp_path / "dest.db")
    try:
        name = copied.execute("SELECT first_name FROM users WHERE telegram_id = 42").fetchone()[0]
    finally:
        copied.close()
    assert name == "Grace"


async def test_reset_replaces_destination_rows(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(source, users=[(1, 42, "Ada", "ada", 0, "en")])
    destination = _url(tmp_path / "dest.db")
    await migrate(source, destination)
    copied = sqlite3.connect(tmp_path / "dest.db")
    copied.execute(
        "INSERT INTO users (id, telegram_id, first_name, is_admin, language_code) "
        "VALUES (8, 99, 'Extra', 0, 'en')"
    )
    copied.commit()
    copied.close()
    report = await migrate(source, destination, mode="reset", verify=True)
    assert report.verify_errors == []
    copied = sqlite3.connect(tmp_path / "dest.db")
    try:
        ids = {row[0] for row in copied.execute("SELECT telegram_id FROM users")}
    finally:
        copied.close()
    assert ids == {42}


async def test_verify_fails_when_destination_has_extra_rows(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(source, users=[(1, 42, "Ada", "ada", 0, "en")])
    destination = _url(tmp_path / "dest.db")
    await migrate(source, destination)
    copied = sqlite3.connect(tmp_path / "dest.db")
    copied.execute(
        "INSERT INTO users (id, telegram_id, first_name, is_admin, language_code) "
        "VALUES (8, 99, 'Extra', 0, 'en')"
    )
    copied.commit()
    copied.close()
    report = await migrate(source, destination, verify=True)
    assert any(error.startswith("users:") for error in report.verify_errors)


def test_failed_row_is_recorded_and_main_exits_nonzero(tmp_path, monkeypatch, caplog):
    source = tmp_path / "legacy.db"
    secret = "legacy-secret-session-value"
    _write_source(
        source,
        plans=[(1, None, 1, 1.0, 30, 1, 1, 1)],
        accounts=[(3, None, "+1555", secret, 0)],
    )
    destination = _url(tmp_path / "dest.db")
    monkeypatch.setenv("DATABASE_URL", destination)
    with caplog.at_level(logging.ERROR, logger="src.tools.migrate_sqlite"):
        code = main(["--sqlite", str(source)])
    assert code == 1
    assert secret not in caplog.text
    assert "session_string" not in caplog.text
    assert any("table=plans" in record.message for record in caplog.records)
    assert any("table=managed_accounts" in record.message for record in caplog.records)


async def test_verify_passes_for_a_clean_copy(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(source, plans=[(9, "Fresh", 10, 1.0, 30, 1, 1, 1)])
    destination = _url(tmp_path / "dest.db")
    report = await migrate(source, destination, verify=True)
    assert report.verify_errors == []
    assert (await _plugins(destination))[9] == set(DEFAULT_PLAN_PLUGINS)


async def test_verify_flags_a_plan_with_no_plugins(tmp_path, monkeypatch):
    source = tmp_path / "legacy.db"
    _write_source(source, plans=[(4, "Bare", 1, 1.0, 30, 1, 1, 1)])

    async def _grant_nothing(_session):
        return 0

    monkeypatch.setattr("src.tools.migrate_sqlite._grant_default_plugins", _grant_nothing)
    report = await migrate(source, _url(tmp_path / "dest.db"), verify=True)
    assert any("has no plugin rows" in error for error in report.verify_errors)


def test_main_rejects_upsert_and_reset_together(tmp_path, capsys):
    source = tmp_path / "legacy.db"
    _write_source(source, users=[(1, 42, "Ada", "ada", 0, "en")])
    code = main(["--sqlite", str(source), "--upsert", "--reset"])
    assert code == 2
    assert "only one" in capsys.readouterr().err


def test_resolve_database_url_reads_dotenv_when_unset(tmp_path, monkeypatch):
    url = _url(tmp_path / "from-dotenv.db")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "BOT_TOKEN=1:test",
                "API_ID=1",
                "API_HASH=hash",
                "ADMIN_IDS=1",
                f"DATABASE_URL={url}",
                f"SESSION_ENCRYPTION_KEY={_FERNET}",
            ]
        )
        + "\n"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        assert resolve_database_url() == url
    finally:
        get_settings.cache_clear()


def test_resolve_database_url_prefers_process_environment(tmp_path, monkeypatch):
    url = _url(tmp_path / "from-env.db")
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///ignored.db\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    assert resolve_database_url() == url


def test_default_plugins_include_phase2_and_phase3():
    for name in ("ping", "id", "help", "groups", "codemon", "admin", "games"):
        assert name in DEFAULT_PLAN_PLUGINS


async def test_session_strings_stay_encrypted(tmp_path):
    source = tmp_path / "legacy.db"
    _write_source(
        source,
        users=[(1, 42, "Ada", "ada", 0, "en")],
        accounts=[(7, 1, "+100", "legacy-session", 0)],
    )
    destination = _url(tmp_path / "dest.db")
    report = await migrate(source, destination)
    assert report["managed_accounts"] == 1
    copied = sqlite3.connect(tmp_path / "dest.db")
    try:
        stored = copied.execute(
            "SELECT session_string FROM managed_accounts WHERE id = 7"
        ).fetchone()[0]
    finally:
        copied.close()
    assert stored != "legacy-session"
    assert decrypt_session(stored) == "legacy-session"
