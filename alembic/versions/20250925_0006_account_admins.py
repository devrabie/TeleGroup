"""Per-account command admins.

Revision ID: 0006_account_admins
Revises: 0005_plan_grants
Create Date: 2026-09-25

Adds ``account_admins`` when it is missing (fresh installs already have it
from ``0001_initial`` once the model exists) and grants the ``delegates``
plugin to plans that already exist. Existing allowlist rows are kept.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_account_admins"
down_revision: str | Sequence[str] | None = "0005_plan_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    if "account_admins" not in _tables(bind):
        op.create_table(
            "account_admins",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=True),
            sa.Column("added_by_telegram_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("account_id", "telegram_id", name="uq_account_admins_pair"),
        )
    from src.plan_grants import DELEGATES_PLUGIN_NAMES, grant_named_plugins

    grant_named_plugins(bind, DELEGATES_PLUGIN_NAMES)


def downgrade() -> None:
    from src.plan_grants import DELEGATES_PLUGIN_NAMES, revoke_named_plugins

    bind = op.get_bind()
    revoke_named_plugins(bind, DELEGATES_PLUGIN_NAMES)
    if "account_admins" in _tables(bind):
        op.drop_table("account_admins")
