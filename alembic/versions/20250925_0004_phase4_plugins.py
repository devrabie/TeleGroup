"""Grant phase 4 plugins to plans that already exist.

Revision ID: 0004_phase4
Revises: 0003_phase3
Create Date: 2026-09-25

Existing allowlist rows are left in place. Downgrade removes only the phase 4
names.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_phase4"
down_revision: str | Sequence[str] | None = "0003_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from src.plan_grants import PHASE4_PLUGIN_NAMES, grant_named_plugins

    grant_named_plugins(op.get_bind(), PHASE4_PLUGIN_NAMES)


def downgrade() -> None:
    from src.plan_grants import PHASE4_PLUGIN_NAMES, revoke_named_plugins

    revoke_named_plugins(op.get_bind(), PHASE4_PLUGIN_NAMES)
