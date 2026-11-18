"""GTINs: the barcode number on a material, a base or a variation, as 14
digits, unique in the organization within each table; across the three
tables, ``hoard.api.items.gtin_written`` refuses one another holds.

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core import search

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("materials", "bases", "variations")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("gtin", sa.Text(), nullable=True))
        op.create_check_constraint(f"ck_{table}_gtin", table, "gtin ~ '^[0-9]{14}$'")
        op.create_index(
            f"uq_{table}_gtin",
            table,
            ["organization_id", "gtin"],
            unique=True,
            postgresql_where=sa.text("gtin IS NOT NULL"),
        )


def downgrade() -> None:
    # The search triggers read the column; the next apply makes them again.
    for kind in ("material", "base", "product"):
        search.drop_source(kind)
    for table in _TABLES:
        op.drop_index(f"uq_{table}_gtin", table_name=table)
        op.drop_constraint(f"ck_{table}_gtin", table, type_="check")
        op.drop_column(table, "gtin")
