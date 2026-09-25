"""Manual plan grants and single-use activation codes.

Revision ID: 0005_plan_grants
Revises: 0004_phase4
Create Date: 2026-09-25

Additive only. Follows ``0004_phase4`` so this stays a single head after the
phase 4 plugin revision. ``0001_initial`` creates every model that is imported
at upgrade time. On a database that is already at ``0004_phase4`` these tables
are new, so this revision creates them. On a fresh upgrade they may already
exist and are left in place.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_plan_grants"
down_revision: str | Sequence[str] | None = "0004_phase4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    if "activation_codes" not in existing:
        op.create_table(
            "activation_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("duration_days", sa.Integer(), nullable=False),
            sa.Column("created_by_telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("used_by_user_id", sa.Integer(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by_telegram_id", sa.BigInteger(), nullable=True),
            sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
            sa.ForeignKeyConstraint(["used_by_user_id"], ["users.id"]),
            sa.UniqueConstraint("code", name="uq_activation_codes_code"),
        )
    if "plan_grants" not in existing:
        op.create_table(
            "plan_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
            sa.Column("target_user_id", sa.Integer(), nullable=False),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("duration_days", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("activation_code_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["activation_code_id"], ["activation_codes.id"]),
            sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"]),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = _tables(bind)
    for name in ("plan_grants", "activation_codes"):
        if name in existing:
            op.drop_table(name)
