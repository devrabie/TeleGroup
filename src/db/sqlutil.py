"""Dialect-specific INSERT helpers.

SQLAlchemy 2.1 removed ``on_conflict_do_nothing`` from the generic Insert
construct, so callers must use the PostgreSQL or SQLite dialect insert.
"""

from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection


def insert_for(bind: Connection | AsyncConnection, model: Any) -> Any:
    dialect_name = bind.dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgres_insert

        return postgres_insert(model)
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(model)
    raise RuntimeError(f"Unsupported database dialect: {dialect_name}")
