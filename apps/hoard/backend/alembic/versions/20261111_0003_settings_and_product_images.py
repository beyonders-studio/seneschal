"""Each organization's settings, and the images of its products.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import isolate, reference, stamp

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_settings_currency"),
    )
    isolate("settings")
    stamp("settings")
    op.create_unique_constraint("uq_settings_organization_id", "settings", ["organization_id"])

    op.create_table(
        "product_images",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.CheckConstraint(
            "content_type IN ('image/gif', 'image/jpeg', 'image/png', 'image/webp')",
            name="ck_product_images_content_type",
        ),
        sa.CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 5242880", name="ck_product_images_size"
        ),
    )
    isolate("product_images")
    stamp("product_images")
    reference("product_images", "product_id", "products", ondelete="CASCADE")
    op.create_index(
        "ix_product_images_product",
        "product_images",
        ["organization_id", "product_id", "position"],
    )


def downgrade() -> None:
    op.drop_table("product_images")
    op.drop_table("settings")
