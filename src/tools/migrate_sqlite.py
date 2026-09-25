"""Copy an existing SQLite ``bot.db`` into the database from ``DATABASE_URL``.

Session strings are encrypted during the copy. The destination should be empty
(apply ``alembic upgrade head`` first). By default, rows that already exist are
left in place and are not updated. ``--upsert`` updates them, and ``--reset``
deletes the copied tables before inserting. A second plain run is not a sync.

Plans copied after Alembic 0002 have no ``plan_plugins`` rows, so no account
would start. Plans that still have an empty allowlist receive the default
plugins. Plans that already have any plugin row are left unchanged.

Failed inserts are logged and the process exits non-zero. ``--verify`` compares
row counts and reports plans that still have no plugins.

    DATABASE_URL=postgresql+asyncpg://... SESSION_ENCRYPTION_KEY=... \\
        python -m src.tools.migrate_sqlite --sqlite data/bot.db --verify
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.crypto import encrypt_session
from src.db.models import (
    AccountManager,
    Base,
    DeviceProfile,
    GroupCreationLog,
    InfoPage,
    ManagedAccount,
    Plan,
    PlanPlugin,
    Proxy,
    SharingToken,
    Subscription,
    User,
)
from src.db.sqlutil import insert_for
from src.runtime.builtin_plugins import DEFAULT_PLAN_PLUGINS

log = logging.getLogger(__name__)

Mode = Literal["skip", "upsert", "reset"]

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

# Children first so ``--reset`` satisfies PostgreSQL foreign keys.
_RESET_TABLES = (
    "auto_replies",
    "pm_permits",
    "chat_locks",
    "plugin_settings",
    "account_plugins",
    "plan_plugins",
    "session_leases",
    "runtime_signals",
    "sharing_tokens",
    "account_managers",
    "group_creation_log",
    "managed_accounts",
    "subscriptions",
    "info_pages",
    "device_profiles",
    "proxies",
    "plans",
    "users",
)

_BOOL_COLUMNS = {
    "is_admin",
    "is_active",
    "is_working",
    "is_running",
    "code_monitor_enabled",
}


@dataclass
class RowFailure:
    table: str
    label: str
    error: str


@dataclass
class MigrateReport:
    """Counts for one copy. ``report[table]`` is the number of inserted rows."""

    inserted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    failed: dict[str, int] = field(default_factory=dict)
    failures: list[RowFailure] = field(default_factory=list)
    source_counts: dict[str, int] = field(default_factory=dict)
    dest_counts: dict[str, int] = field(default_factory=dict)
    plugins_granted: int = 0
    verify_errors: list[str] = field(default_factory=list)

    def __getitem__(self, table: str) -> int:
        return self.inserted[table]


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


def _conflict_keys(table: str) -> list[str]:
    if table == "info_pages":
        return ["page_key", "lang_code"]
    if table == "users":
        return ["telegram_id"]
    if table == "plans":
        return ["name"]
    if table == "proxies":
        return ["proxy_string"]
    if table == "sharing_tokens":
        return ["token"]
    return ["id"]


def _row_label(table: str, row: dict[str, Any]) -> str:
    """Identify a row in logs without session strings or other secrets."""
    if table == "users":
        return f"telegram_id={row.get('telegram_id')}"
    if table == "managed_accounts":
        return f"id={row.get('id')} phone={row.get('phone')}"
    if table == "plans":
        return f"id={row.get('id')} name={row.get('name')}"
    if table == "proxies":
        return f"id={row.get('id')}"
    if table == "info_pages":
        return f"{row.get('page_key')}/{row.get('lang_code')}"
    if "id" in row:
        return f"id={row.get('id')}"
    return "row"


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


def _insert_statement(bind: Any, model: Any, table: str, row: dict[str, Any], mode: Mode) -> Any:
    stmt = insert_for(bind, model).values(**row)
    keys = _conflict_keys(table)
    if mode == "upsert":
        assignments = {
            column: stmt.excluded[column] for column in row if column not in keys and column != "id"
        }
        if assignments:
            return stmt.on_conflict_do_update(index_elements=keys, set_=assignments)
    return stmt.on_conflict_do_nothing(index_elements=keys)


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


async def _delete_copied_tables(session: AsyncSession) -> None:
    for table in _RESET_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))


async def _grant_default_plugins(session: AsyncSession) -> int:
    """Fill allowlists that are still empty. Existing rows are not changed."""
    plan_ids = list((await session.scalars(select(Plan.id))).all())
    granted = 0
    for plan_id in plan_ids:
        existing = await session.scalar(
            select(func.count()).select_from(PlanPlugin).where(PlanPlugin.plan_id == plan_id)
        )
        if existing:
            continue
        for name in DEFAULT_PLAN_PLUGINS:
            session.add(PlanPlugin(plan_id=plan_id, plugin_name=name))
            granted += 1
    return granted


async def _collect_verify_errors(
    session: AsyncSession, source_counts: dict[str, int]
) -> tuple[dict[str, int], list[str]]:
    dest_counts: dict[str, int] = {}
    errors: list[str] = []
    for table, model in _TABLES:
        dest = int(await session.scalar(select(func.count()).select_from(model)) or 0)
        dest_counts[table] = dest
        source = source_counts.get(table, 0)
        if dest != source:
            errors.append(f"{table}: destination has {dest} rows, source has {source}")
    empty_plans = list(
        (
            await session.scalars(
                select(Plan.id).where(
                    ~select(PlanPlugin.plan_id).where(PlanPlugin.plan_id == Plan.id).exists()
                )
            )
        ).all()
    )
    for plan_id in empty_plans:
        errors.append(f"plan {plan_id} has no plugin rows")
    return dest_counts, errors


async def migrate(
    sqlite_path: Path,
    database_url: str | None = None,
    *,
    mode: Mode = "skip",
    verify: bool = False,
) -> MigrateReport:
    from src.config import resolve_database_url

    engine = create_async_engine(database_url or resolve_database_url())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    source = _read_sqlite(sqlite_path)
    report = MigrateReport(source_counts={table: len(rows) for table, rows in source.items()})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with maker() as session:
        bind = await session.connection()
        if mode == "reset":
            await _delete_copied_tables(session)
        for table, model in _TABLES:
            inserted = 0
            skipped = 0
            failed = 0
            for row in source.get(table, []):
                if not row:
                    skipped += 1
                    continue
                label = _row_label(table, row)
                try:
                    async with session.begin_nested():
                        result = await session.execute(
                            _insert_statement(bind, model, table, row, mode)
                        )
                        if int(getattr(result, "rowcount", 0) or 0) > 0:
                            inserted += 1
                        else:
                            skipped += 1
                except Exception as exc:
                    failed += 1
                    error = type(exc).__name__
                    report.failures.append(RowFailure(table, label, error))
                    log.error(
                        "migrate failed table=%s row=%s error=%s",
                        table,
                        label,
                        error,
                    )
            report.inserted[table] = inserted
            report.skipped[table] = skipped
            report.failed[table] = failed
        report.plugins_granted = await _grant_default_plugins(session)
        await session.flush()
        report.dest_counts, report.verify_errors = await _collect_verify_errors(
            session, report.source_counts
        )
        if not verify:
            report.verify_errors = []
        await session.commit()
        connection = await session.connection()
        await _reset_sequences(connection)
        await session.commit()
    await engine.dispose()
    return report


def _print_report(report: MigrateReport, *, verify: bool) -> None:
    for table, _model in _TABLES:
        print(
            f"{table}: inserted {report.inserted.get(table, 0)}, "
            f"skipped {report.skipped.get(table, 0)}, "
            f"failed {report.failed.get(table, 0)}"
        )
    print(f"plugins granted: {report.plugins_granted}")
    for failure in report.failures:
        print(
            f"FAILED {failure.table} {failure.label}: {failure.error}",
            file=sys.stderr,
        )
    if report.failures:
        print(f"{len(report.failures)} row(s) failed", file=sys.stderr)
    if verify:
        for error in report.verify_errors:
            print(f"VERIFY {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Copy SQLite bot.db into DATABASE_URL. "
            "Target an empty database unless you pass --upsert or --reset."
        )
    )
    parser.add_argument("--sqlite", required=True, help="Path to the existing bot.db file")
    parser.add_argument(
        "--upsert",
        action="store_true",
        help="Update rows that already exist instead of leaving them unchanged",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete copied tables and plugin rows, then insert the SQLite data",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare source and destination row counts and require every plan to have plugins",
    )
    args = parser.parse_args(argv)
    if args.upsert and args.reset:
        print("Choose only one of --upsert and --reset", file=sys.stderr)
        return 2
    path = Path(args.sqlite)
    if not path.is_file():
        print(f"SQLite database not found: {path}", file=sys.stderr)
        return 2
    mode: Mode = "skip"
    if args.upsert:
        mode = "upsert"
    elif args.reset:
        mode = "reset"
    report = asyncio.run(migrate(path, mode=mode, verify=args.verify))
    _print_report(report, verify=args.verify)
    if report.failures or (args.verify and report.verify_errors):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
