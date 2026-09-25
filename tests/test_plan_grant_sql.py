"""Plugin allowlist inserts must bind each name once.

PostgreSQL deduces a single type for a prepared-statement parameter. The old
``INSERT ... SELECT :name ... WHERE plugin_name = :name`` statement used that
bind both as an untyped select item and as a ``varchar`` comparison, which
raises ``inconsistent types deduced for parameter``. There is no Postgres
fixture in this suite; ``POSTGRES_TEST_URL`` runs the same statements against
a real server when it is set.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.plan_grants import INSERT_PLAN_PLUGIN_SQL, PHASE4_PLUGIN_NAMES, PLAN_PLUGIN_EXISTS_SQL

ROOT = Path(__file__).resolve().parents[1]
_REUSED_NAME = "SELECT :plan_id, :name"
_BUILTIN_PLUGINS = ("ping", "id", "help", "groups", "codemon")


def test_grant_sql_binds_each_name_once():
    for sql in (INSERT_PLAN_PLUGIN_SQL, PLAN_PLUGIN_EXISTS_SQL):
        assert sql.count(":name") == 1
        assert sql.count(":plan_id") == 1
    assert INSERT_PLAN_PLUGIN_SQL == (
        "INSERT INTO plan_plugins (plan_id, plugin_name) VALUES (:plan_id, :name)"
    )
    assert "NOT EXISTS" not in INSERT_PLAN_PLUGIN_SQL
    for folder in ("alembic", "src"):
        for path in (ROOT / folder).rglob("*.py"):
            assert _REUSED_NAME not in path.read_text(), path
    migration = (ROOT / "alembic" / "versions" / "20250925_0002_plugin_runtime.py").read_text()
    assert "grant_named_plugins" in migration
    phase4 = (ROOT / "alembic" / "versions" / "20250925_0004_phase4_plugins.py").read_text()
    grants = (ROOT / "alembic" / "versions" / "20250925_0005_plan_grants.py").read_text()
    assert 'revision: str = "0004_phase4"' in phase4
    assert 'down_revision: str | Sequence[str] | None = "0003_phase3"' in phase4
    assert 'revision: str = "0005_plan_grants"' in grants
    assert 'down_revision: str | Sequence[str] | None = "0004_phase4"' in grants


def test_phase4_grant_on_postgres(monkeypatch):
    url = os.environ.get("POSTGRES_TEST_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_URL is not set")
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    asyncio.run(_reset_schema(url))
    command.upgrade(cfg, "0001_initial")
    asyncio.run(_insert_plan(url))
    asyncio.run(_expect_reused_name_fails(url))
    command.upgrade(cfg, "0002_plugin_runtime")
    command.upgrade(cfg, "0004_phase4")
    names = asyncio.run(_plugin_names(url))
    assert set(_BUILTIN_PLUGINS) <= names
    assert set(PHASE4_PLUGIN_NAMES) <= names
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")
    version = asyncio.run(_alembic_version(url))
    assert version == "0005_plan_grants"
    assert asyncio.run(_plugin_names(url)) == names


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _insert_plan(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO plans (name, price_stars, price_usd, duration_days, "
                    "max_accounts, daily_group_limit, is_active) "
                    "VALUES ('Legacy', 1, 1, 30, 1, 1, true)"
                )
            )
    finally:
        await engine.dispose()


async def _expect_reused_name_fails(url: str) -> None:
    engine = create_async_engine(url)
    reused = text(
        "INSERT INTO plan_plugins (plan_id, plugin_name) "
        "SELECT :plan_id, :name WHERE NOT EXISTS ("
        "SELECT 1 FROM plan_plugins WHERE plan_id = :plan_id AND plugin_name = :name)"
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(reused, {"plan_id": 1, "name": "download"})
    except Exception as exc:
        assert "inconsistent types deduced for parameter" in str(exc)
        return
    finally:
        await engine.dispose()
    raise AssertionError("reused :name bind should fail on PostgreSQL")


async def _plugin_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT plugin_name FROM plan_plugins"))).all()
    finally:
        await engine.dispose()
    return {row[0] for row in rows}


async def _alembic_version(url: str) -> str:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT version_num FROM alembic_version"))).one()
    finally:
        await engine.dispose()
    return str(row[0])
