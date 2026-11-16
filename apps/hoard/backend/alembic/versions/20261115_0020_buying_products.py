"""Purchase lines name any item, so a product's variation can be bought as a
material is; and variations have a supplier, a supplier code, a purchase unit
and a purchase conversion, as materials do.

Every line keeps the material it names, now as its item.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core import search
from seneschal_core.migrations import reference

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The purchases search reads the column by name; the next start makes it again.
    search.drop_source("purchase")
    op.drop_constraint("fk_purchase_lines_material_id", "purchase_lines")
    op.alter_column("purchase_lines", "material_id", new_column_name="item_id")
    reference("purchase_lines", "item_id", "items")

    op.add_column("variations", sa.Column("supplier_contact_id", sa.BigInteger(), nullable=True))
    op.add_column("variations", sa.Column("supplier_code", sa.Text(), nullable=True))
    op.add_column("variations", sa.Column("purchase_unit_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "variations",
        sa.Column("purchase_conversion", sa.Numeric(20, 6), nullable=False, server_default="1"),
    )
    reference("variations", "purchase_unit_id", "units")
    op.create_check_constraint(
        "ck_variations_purchase_conversion", "variations", "purchase_conversion > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_variations_purchase_conversion", "variations")
    op.drop_constraint("fk_variations_purchase_unit_id", "variations")
    for column in (
        "purchase_conversion",
        "purchase_unit_id",
        "supplier_code",
        "supplier_contact_id",
    ):
        op.drop_column("variations", column)

    search.drop_source("purchase")
    # A line that bought a variation has no material to name.
    op.execute("ALTER TABLE purchase_lines NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute(
            "DELETE FROM purchase_lines pl WHERE NOT EXISTS "
            "(SELECT 1 FROM materials m WHERE m.id = pl.item_id)"
        )
    finally:
        op.execute("ALTER TABLE purchase_lines FORCE ROW LEVEL SECURITY")
    op.drop_constraint("fk_purchase_lines_item_id", "purchase_lines")
    op.alter_column("purchase_lines", "item_id", new_column_name="material_id")
    reference("purchase_lines", "material_id", "materials")
