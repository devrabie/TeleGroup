"""Plugin toggles, plan allowlists, runtime signals, and session leases.

Revision ID: 0002_plugin_runtime
Revises: 0001_initial
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_plugin_runtime"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep this tuple aligned with the built-in plugins in src/plugins.
_BUILTIN_PLUGINS = ("ping", "id", "help", "groups", "codemon")


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "plan_plugins" not in existing:
        op.create_table(
            "plan_plugins",
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("plugin_name", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("plan_id", "plugin_name"),
        )
    if "account_plugins" not in existing:
        op.create_table(
            "account_plugins",
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("plugin_name", sa.String(length=64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id", "plugin_name"),
        )
    if "plugin_settings" not in existing:
        op.create_table(
            "plugin_settings",
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("plugin_name", sa.String(length=64), nullable=False),
            sa.Column("setting_key", sa.String(length=64), nullable=False),
            sa.Column("setting_value", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id", "plugin_name", "setting_key"),
        )
    if "runtime_signals" not in existing:
        op.create_table(
            "runtime_signals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("account_id", sa.Integer(), nullable=True),
            sa.Column("event", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
    if "session_leases" not in existing:
        op.create_table(
            "session_leases",
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("holder", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("acked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id"),
        )
    _grant_builtin_plugins(bind)


def _grant_builtin_plugins(bind: sa.Connection) -> None:
    """Existing plans keep today's features until an admin edits the allowlist.

    Uses ``grant_named_plugins`` so ``:name`` is bound once per statement.
    The previous ``INSERT ... SELECT ... WHERE NOT EXISTS`` reused ``:name``
    and PostgreSQL raised ``inconsistent types deduced for parameter``.
    """
    from src.plan_grants import grant_named_plugins

    grant_named_plugins(bind, _BUILTIN_PLUGINS)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    for name in (
        "session_leases",
        "runtime_signals",
        "plugin_settings",
        "account_plugins",
        "plan_plugins",
    ):
        if name in existing:
            op.drop_table(name)
