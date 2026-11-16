"""Price points, each variation's price at them and each contact's; the
overhead on what goods cost; and the currency a store's order is in.

A store's order copied in before this reads as in the organization's currency
until the store's sync copies it again.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _amount(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.Numeric(20, 6), nullable=nullable, server_default=None if nullable else "0"
    )


def upgrade() -> None:
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

    op.add_column("settings", _amount("overhead", nullable=True))
    op.create_check_constraint("ck_settings_overhead", "settings", "overhead >= 0")
    op.add_column("orders", sa.Column("currency", sa.Text(), nullable=True))
    op.create_check_constraint("ck_orders_currency", "orders", "currency ~ '^[A-Z]{3}$'")


def downgrade() -> None:
    op.drop_constraint("ck_orders_currency", "orders")
    op.drop_column("orders", "currency")
    op.drop_constraint("ck_settings_overhead", "settings")
    op.drop_column("settings", "overhead")
    op.drop_table("contact_price_points")
    op.drop_table("variation_prices")
    op.drop_table("price_points")
