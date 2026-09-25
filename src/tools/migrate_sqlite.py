"""Copy an existing SQLite ``bot.db`` into the database from ``DATABASE_URL``.

Session strings are encrypted during the copy. Rows that already exist are
left in place. Run once before starting the bot against PostgreSQL:

    DATABASE_URL=postgresql+asyncpg://... SESSION_ENCRYPTION_KEY=... \\
        python -m src.tools.migrate_sqlite --sqlite data/bot.db
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.crypto import encrypt_session
from src.db.models import (
    AccountManager,
    Base,
    DeviceProfile,
    GroupCreationLog,
    InfoPage,
    ManagedAccount,
    Plan,
    Proxy,
    SharingToken,
    Subscription,
    User,
)
from src.db.sqlutil import insert_for

_TABLES = (
    ("users", User),
    ("plans", Plan),
    ("proxies", Proxy),
    ("device_profiles", DeviceProfile),
    ("info_pages", InfoPage),
    ("subscriptions", Subscription),
    ("managed_accounts", ManagedAccount),
    ("group_creation_log", GroupCreationLog),
    ("account_managers", AccountManager),
    ("sharing_tokens", SharingToken),
)

_BOOL_COLUMNS = {
    "is_admin",
    "is_active",
    "is_working",
    "is_running",
    "code_monitor_enabled",
}


def _parse_dt(value: Any) -> Any:
    if not isinstance(value, str):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _coerce(column: str, value: Any) -> Any:
    if column in _BOOL_COLUMNS and value is not None:
        return bool(value)
    if (
        column.endswith("_at")
        or column.endswith("_date")
        or column
        in {
            "flood_wait_until",
            "next_creation_time",
            "last_checked",
            "start_date",
            "end_date",
            "creation_timestamp",
        }
    ):
        return _parse_dt(value)
    return value


def _read_sqlite(path: Path) -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    payload: dict[str, list[dict[str, Any]]] = {}
    try:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, model in _TABLES:
            if table not in names:
                payload[table] = []
                continue
            columns = {column.name for column in model.__table__.columns}
            rows = []
            for record in connection.execute(f"SELECT * FROM {table}"):
                item = {}
                for key in record.keys():
                    if key in columns:
                        item[key] = _coerce(key, record[key])
                if table == "managed_accounts" and item.get("session_string"):
                    item["session_string"] = encrypt_session(item["session_string"])
                rows.append(item)
            payload[table] = rows
    finally:
        connection.close()
    return payload


async def _reset_sequences(connection: Any) -> None:
    if connection.dialect.name != "postgresql":
        return
    for table, model in _TABLES:
        if "id" not in model.__table__.columns:
            continue
        await connection.execute(
            text(
                "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                "COALESCE((SELECT MAX(id) FROM "
                + table
                + "), 1), (SELECT COUNT(*) > 0 FROM "
                + table
                + "))"
            ),
            {"table_name": table},
        )


async def migrate(sqlite_path: Path, database_url: str | None = None) -> dict[str, int]:
    from src.config import get_settings

    engine = create_async_engine(database_url or get_settings().database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    source = _read_sqlite(sqlite_path)
    inserted: dict[str, int] = {}
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with maker() as session:
        bind = await session.connection()
        for table, model in _TABLES:
            count = 0
            for row in source.get(table, []):
                if not row:
                    continue
                try:
                    async with session.begin_nested():
                        stmt = insert_for(bind, model).values(**row)
                        conflict = []
                        if table == "info_pages":
                            conflict = ["page_key", "lang_code"]
                        elif table == "users":
                            conflict = ["telegram_id"]
                        elif table == "plans":
                            conflict = ["name"]
                        elif table == "proxies":
                            conflict = ["proxy_string"]
                        elif table == "sharing_tokens":
                            conflict = ["token"]
                        if conflict:
                            stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
                        else:
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                        result = await session.execute(stmt)
                        if int(getattr(result, "rowcount", 0) or 0) > 0:
                            count += 1
                except Exception:
                    continue
            inserted[table] = count
        await session.commit()
        connection = await session.connection()
        await _reset_sequences(connection)
        await session.commit()
    await engine.dispose()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy SQLite bot.db into DATABASE_URL")
    parser.add_argument("--sqlite", required=True, help="Path to the existing bot.db file")
    args = parser.parse_args()
    path = Path(args.sqlite)
    if not path.is_file():
        raise SystemExit(f"SQLite database not found: {path}")
    counts = asyncio.run(migrate(path))
    for table, count in counts.items():
        print(f"{table}: inserted {count}")


if __name__ == "__main__":
    main()
