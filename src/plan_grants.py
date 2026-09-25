"""Allowlist rows for plugins shipped after a plan already exists.

Alembic revision 0004 calls ``grant_named_plugins``. The insert skips names
that are already present and never deletes a row.

Each statement binds ``:name`` once. PostgreSQL rejects the same parameter
when one use is an untyped ``SELECT`` item and another is compared with
``plugin_name`` (``varchar``): ``inconsistent types deduced for parameter``.
"""

from __future__ import annotations

from typing import Any

# Existence is checked first, then a plain INSERT. Reusing ``:name`` in
# ``INSERT ... SELECT ... WHERE NOT EXISTS`` is what PostgreSQL rejects.
INSERT_PLAN_PLUGIN_SQL = "INSERT INTO plan_plugins (plan_id, plugin_name) VALUES (:plan_id, :name)"
PLAN_PLUGIN_EXISTS_SQL = (
    "SELECT 1 FROM plan_plugins WHERE plan_id = :plan_id AND plugin_name = :name"
)

# Granted by revision 0006 so existing plans can use account-admin commands.
DELEGATES_PLUGIN_NAMES: tuple[str, ...] = ("delegates",)

PHASE4_PLUGIN_NAMES: tuple[str, ...] = (
    "download",
    "stickers",
    "translate",
    "tts",
    "ocr",
    "convert",
    "telegraph",
    "info",
    "leave",
    "repeat",
    "profile",
    "clock",
    "calc",
)


def _table_names(bind: Any) -> set[str]:
    import sqlalchemy as sa

    return set(sa.inspect(bind).get_table_names())


def grant_named_plugins(bind: Any, names: tuple[str, ...]) -> int:
    """Insert ``names`` for every plan. Existing allowlist rows stay."""
    import sqlalchemy as sa

    if "plans" not in _table_names(bind) or "plan_plugins" not in _table_names(bind):
        return 0
    plan_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM plans")).fetchall()]
    insert = sa.text(INSERT_PLAN_PLUGIN_SQL)
    exists = sa.text(PLAN_PLUGIN_EXISTS_SQL)
    added = 0
    for plan_id in plan_ids:
        for name in names:
            params = {"plan_id": plan_id, "name": name}
            if bind.execute(exists, params).first():
                continue
            bind.execute(insert, params)
            added += 1
    return added


def revoke_named_plugins(bind: Any, names: tuple[str, ...]) -> None:
    """Remove only ``names``. Other allowlist rows stay. Used by downgrade."""
    import sqlalchemy as sa

    if "plan_plugins" not in _table_names(bind):
        return
    delete = sa.text("DELETE FROM plan_plugins WHERE plugin_name = :name")
    for name in names:
        bind.execute(delete, {"name": name})
