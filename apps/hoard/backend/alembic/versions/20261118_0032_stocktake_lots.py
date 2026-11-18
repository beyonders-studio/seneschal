"""Stocktakes by lot: how a stocktake counts lots (``stocktakes.lot_count``:
a full count or a spot check, chosen when it is started), and the lot each
of its rows counts (``stocktake_rows.lot_id``), one row per item and lot.

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import reference

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgresql.ENUM("full", "spot", name="lot_count").create(op.get_bind())
    op.add_column(
        "stocktakes",
        sa.Column("lot_count", postgresql.ENUM(name="lot_count", create_type=False), nullable=True),
    )
    op.add_column("stocktake_rows", sa.Column("lot_id", sa.BigInteger(), nullable=True))
    reference("stocktake_rows", "lot_id", "lots", ondelete="CASCADE")
    op.drop_constraint("uq_stocktake_rows_item", "stocktake_rows", type_="unique")
    op.create_index(
        "uq_stocktake_rows_item_lot",
        "stocktake_rows",
        ["organization_id", "stocktake_id", "item_id", "lot_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_stocktake_rows_item_lot", table_name="stocktake_rows")
    # A row of a lot has no place in a stocktake that counts each item once.
    op.execute("DELETE FROM stocktake_rows WHERE lot_id IS NOT NULL")
    op.create_unique_constraint(
        "uq_stocktake_rows_item", "stocktake_rows", ["organization_id", "stocktake_id", "item_id"]
    )
    op.drop_constraint("fk_stocktake_rows_lot_id", "stocktake_rows", type_="foreignkey")
    op.drop_column("stocktake_rows", "lot_id")
    op.drop_column("stocktakes", "lot_count")
    postgresql.ENUM(name="lot_count").drop(op.get_bind())
