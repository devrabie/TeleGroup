"""Initial schema for the newest TeleGroup branch.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op
from src.db.models import Base

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
