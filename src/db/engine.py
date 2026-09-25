"""Async engine bound to a dedicated loop so existing sync call sites keep working."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import get_settings

_DEFAULT_DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "bot.db"
DB_FILE = _DEFAULT_DB_FILE

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_loop_lock = threading.Lock()
_engine: AsyncEngine | None = None
_maker: async_sessionmaker[AsyncSession] | None = None
_url: str | None = None


def current_database_url() -> str:
    """Tests patch ``DB_FILE``; production uses ``DATABASE_URL``."""
    configured = Path(DB_FILE).expanduser()
    try:
        overridden = configured.resolve() != _DEFAULT_DB_FILE.resolve()
    except OSError:
        overridden = True
    if overridden:
        configured.parent.mkdir(parents=True, exist_ok=True)
        return "sqlite+aiosqlite:///" + configured.resolve().as_posix()
    return get_settings().database_url


def sqlite_file_path(url: str | None = None) -> Path | None:
    target = current_database_url() if url is None else url
    prefix = "sqlite+aiosqlite:///"
    if not target.startswith(prefix):
        return None
    raw = target[len(prefix) :]
    if raw in {"", ":memory:"}:
        return None
    return Path(raw)


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def _run() -> None:
            assert _loop is not None
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _thread = threading.Thread(target=_run, name="telegroup-db", daemon=True)
        _thread.start()
        return _loop


def run_db(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on the database loop and return its result."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def _apply_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


async def sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _maker, _url
    url = current_database_url()
    if _maker is not None and _url == url:
        return _maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _maker = None
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    _engine = engine
    _maker = async_sessionmaker(engine, expire_on_commit=False)
    _url = url
    return _maker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    maker = await sessionmaker()
    session = maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine_async() -> None:
    global _engine, _maker, _url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _maker = None
    _url = None


def dispose_engine() -> None:
    if _loop is None or not _loop.is_running():
        return
    run_db(dispose_engine_async())


def get_engine() -> AsyncEngine | None:
    return _engine
