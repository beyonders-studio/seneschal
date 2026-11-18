"""Whether a run makes a base it uses first, from the base's own recipe: all
the run takes, only what it is short of, or none.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bases",
        sa.Column(
            "auto_manufacture",
            postgresql.ENUM(name="auto_manufacture", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("bases", "auto_manufacture")
