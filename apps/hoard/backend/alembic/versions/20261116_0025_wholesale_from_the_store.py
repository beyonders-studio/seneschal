"""Wholesale read from the store, in place of price points: whether a
company bought a store's order. Price points, each variation's price at them
and each contact's go.

Every store reads its orders again on its next reconcile, which marks those a
company bought.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("contact_price_points")
    op.drop_table("variation_prices")
    op.drop_table("price_points")

    op.add_column(
        "orders",
        sa.Column("wholesale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # A store with no stamp reads every order its reconcile can (``hoard.sync``).
    op.execute("ALTER TABLE stores NO FORCE ROW LEVEL SECURITY")
    try:
        op.execute("UPDATE stores SET orders_since = NULL WHERE imported_at IS NOT NULL")
    finally:
        op.execute("ALTER TABLE stores FORCE ROW LEVEL SECURITY")


def _amount(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(20, 6), nullable=False, server_default="0")


def downgrade() -> None:
    op.drop_column("orders", "wholesale")

    op.create_table(
        "price_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        _amount("markup"),
        _amount("listing_fee"),
        _amount("commission"),
        sa.CheckConstraint(
            "markup >= 0 AND listing_fee >= 0 AND commission >= 0 AND commission < 100",
            name="ck_price_points_amounts",
        ),
    )
    isolate("price_points")
    stamp("price_points")
    referable("price_points")
    op.create_unique_constraint("uq_price_points_name", "price_points", ["organization_id", "name"])

    op.create_table(
        "variation_prices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("variation_id", sa.BigInteger(), nullable=False),
        sa.Column("price_point_id", sa.BigInteger(), nullable=False),
        _amount("price"),
        sa.CheckConstraint("price >= 0", name="ck_variation_prices_price"),
    )
    isolate("variation_prices")
    stamp("variation_prices")
    reference("variation_prices", "variation_id", "variations", ondelete="CASCADE")
    reference("variation_prices", "price_point_id", "price_points", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_variation_prices_variation_point",
        "variation_prices",
        ["organization_id", "variation_id", "price_point_id"],
    )

    op.create_table(
        "contact_price_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("price_point_id", sa.BigInteger(), nullable=False),
    )
    isolate("contact_price_points")
    stamp("contact_price_points")
    reference("contact_price_points", "price_point_id", "price_points", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_contact_price_points_contact", "contact_price_points", ["organization_id", "contact_id"]
    )
