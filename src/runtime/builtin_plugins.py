"""Plugin names granted when a plan has no allowlist rows.

Alembic revision 0002 grants the first five names to plans that already exist
when that revision runs. The SQLite importer copies plans afterwards, so those
copies have an empty ``plan_plugins`` set and no account would start. This
tuple is those five plus the phase 3 defaults. Plans that already have any
allowlist row are left unchanged.
"""

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
)
