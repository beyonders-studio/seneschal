"""What a material's or a base's page keeps of it: whether it is archived, its
notes, its picture and its bin at each location; a recipe's notes; and what an
hour of work costs.

Products' status becomes every item's, which materials and bases start
active. The product images become the images, which hold a material's or a
base's one picture too.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core import search
from seneschal_core.migrations import isolate, reference, stamp

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What the product images' table and its parts were called, and are called now.
RENAMED = {
    "product_images_pkey": "images_pkey",
    "fk_product_images_organization_id": "fk_images_organization_id",
    "fk_product_images_product_id": "fk_images_product_id",
    "ck_product_images_content_type": "ck_images_content_type",
    "ck_product_images_size": "ck_images_size",
}
RENAMED_INDEXES = {
    "ix_product_images_organization_id": "ix_images_organization_id",
    "ix_product_images_product": "ix_images_product",
}


def _rename_images(table: str, names: dict[str, str], indexes: dict[str, str]) -> None:
    for was, now in names.items():
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {was} TO {now}")
    for was, now in indexes.items():
        op.execute(f"ALTER INDEX {was} RENAME TO {now}")


def upgrade() -> None:
    op.execute("ALTER TYPE product_status RENAME TO item_status")
    status = postgresql.ENUM(name="item_status", create_type=False)
    for table in ("materials", "bases"):
        op.add_column(table, sa.Column("status", status, nullable=False, server_default="active"))
        op.add_column(table, sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("recipes", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("settings", sa.Column("labour_rate", sa.Numeric(20, 6), nullable=True))
    op.create_check_constraint("ck_settings_labour_rate", "settings", "labour_rate >= 0")

    op.create_table(
        "bins",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.BigInteger(), nullable=False),
        sa.Column("zone", sa.Text(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("position", sa.Text(), nullable=True),
        sa.CheckConstraint("num_nonnulls(zone, section, position) > 0", name="ck_bins_named"),
    )
    isolate("bins")
    stamp("bins")
    reference("bins", "item_id", "items", ondelete="CASCADE")
    reference("bins", "location_id", "locations", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_bins_item_location", "bins", ["organization_id", "item_id", "location_id"]
    )

    op.rename_table("product_images", "images")
    op.execute("ALTER SEQUENCE product_images_id_seq RENAME TO images_id_seq")
    _rename_images("images", RENAMED, RENAMED_INDEXES)
    op.alter_column("images", "product_id", nullable=True)
    op.add_column("images", sa.Column("item_id", sa.BigInteger(), nullable=True))
    reference("images", "item_id", "items", ondelete="CASCADE")
    op.create_check_constraint("ck_images_of", "images", "num_nonnulls(product_id, item_id) = 1")
    op.create_index(
        "uq_images_item",
        "images",
        ["organization_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("item_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Pictures of materials and bases go, with the table's row security lifted
    # while they do.
    op.execute("ALTER TABLE images NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute("DELETE FROM images WHERE item_id IS NOT NULL")
    finally:
        op.execute("ALTER TABLE images FORCE ROW LEVEL SECURITY")
    op.drop_index("uq_images_item", "images")
    op.drop_constraint("ck_images_of", "images")
    op.drop_constraint("fk_images_item_id", "images")
    op.drop_column("images", "item_id")
    op.alter_column("images", "product_id", nullable=False)
    _rename_images(
        "images",
        {now: was for was, now in RENAMED.items()},
        {now: was for was, now in RENAMED_INDEXES.items()},
    )
    op.execute("ALTER SEQUENCE images_id_seq RENAME TO product_images_id_seq")
    op.rename_table("images", "product_images")

    op.drop_table("bins")

    op.drop_constraint("ck_settings_labour_rate", "settings")
    op.drop_column("settings", "labour_rate")
    op.drop_column("recipes", "notes")
    for kind, table in (("material", "materials"), ("base", "bases")):
        search.drop_source(kind)
        op.drop_column(table, "notes")
        op.drop_column(table, "status")
    op.execute("ALTER TYPE item_status RENAME TO product_status")
