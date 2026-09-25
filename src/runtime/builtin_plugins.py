"""Plugin names granted when a plan has no allowlist rows.

Alembic revision 0002 grants the first five names to plans that already exist
when that revision runs. The SQLite importer copies plans afterwards, so those
copies have an empty ``plan_plugins`` set and no account would start. This
tuple is those five, the phase 3 defaults, and the phase 4 names. The importer
still skips plans that already have any allowlist row. Alembic 0004 inserts
the phase 4 names into plans that already exist and does not delete rows.
"""

from src.plan_grants import DELEGATES_PLUGIN_NAMES, PHASE4_PLUGIN_NAMES

DEFAULT_PLAN_PLUGINS: tuple[str, ...] = (
    "ping",
    "id",
    "help",
    "groups",
    "codemon",
    "admin",
    "storage",
    "autoreply",
    "afk",
    "pmpermit",
    "locks",
    "tagall",
    "broadcast",
    "create",
    "gifts",
    "games",
    *PHASE4_PLUGIN_NAMES,
    *DELEGATES_PLUGIN_NAMES,
)
