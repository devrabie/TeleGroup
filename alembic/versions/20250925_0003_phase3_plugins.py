"""Auto-replies, private-message permits, and group locks.

Revision ID: 0003_phase3
Revises: 0002_plugin_runtime
Create Date: 2026-09-25

New plugins are not granted to plans that already exist. Admins add them from
the plan editor. ``add_plan`` grants whatever is registered at creation time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase3"
down_revision: str | Sequence[str] | None = "0002_plugin_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "auto_replies" not in existing:
        op.create_table(
            "auto_replies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("keyword", sa.Text(), nullable=False),
            sa.Column("response", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("account_id", "chat_id", "keyword", name="uq_auto_replies_rule"),
        )
    if "pm_permits" not in existing:
        op.create_table(
            "pm_permits",
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("warnings", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id", "user_id"),
        )
    if "chat_locks" not in existing:
        op.create_table(
            "chat_locks",
            sa.Column("account_id", sa.Integer(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("lock_name", sa.String(length=32), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["managed_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("account_id", "chat_id", "lock_name"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    for name in ("chat_locks", "pm_permits", "auto_replies"):
        if name in existing:
            op.drop_table(name)
