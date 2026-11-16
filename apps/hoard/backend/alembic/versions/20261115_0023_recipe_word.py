"""What the organization calls a recipe, beside what it calls a base.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("recipe_word", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "recipe_word")
