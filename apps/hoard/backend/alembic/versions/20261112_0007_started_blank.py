"""Whether an admin answered on first opening Hoard that the business is none
it has a starting setup for, so it starts blank.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("started_blank", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("settings", "started_blank")
