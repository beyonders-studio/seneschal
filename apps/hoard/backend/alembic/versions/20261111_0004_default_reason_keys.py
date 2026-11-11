"""A default adjustment reason carries a key, and no name until someone gives
it one: pages call it by the translated words for its key.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("adjustment_reasons", sa.Column("key", sa.Text(), nullable=True))
    op.alter_column("adjustment_reasons", "name", nullable=True)
    op.create_unique_constraint(
        "uq_adjustment_reasons_key", "adjustment_reasons", ["organization_id", "key"]
    )
    op.create_check_constraint(
        "ck_adjustment_reasons_name", "adjustment_reasons", "name IS NOT NULL OR key IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint("ck_adjustment_reasons_name", "adjustment_reasons")
    op.drop_constraint("uq_adjustment_reasons_key", "adjustment_reasons")
    # A default reason nobody named takes its key as its name, in every organization.
    op.execute("ALTER TABLE adjustment_reasons NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE adjustment_reasons SET name = key WHERE name IS NULL")
    op.execute("ALTER TABLE adjustment_reasons FORCE ROW LEVEL SECURITY")
    op.alter_column("adjustment_reasons", "name", nullable=False)
    op.drop_column("adjustment_reasons", "key")
