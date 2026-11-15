"""Keeping store orders in sync: each line's current and refunded quantity,
each order's refunded total, the store's customer number and when the store
last changed it, and the body of an order's webhook on its nudge.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _amount(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(20, 6), nullable=False, server_default="0")


def _amounts(table: str, check: str) -> None:
    op.drop_constraint(f"ck_{table}_amounts", table)
    op.create_check_constraint(f"ck_{table}_amounts", table, check)


def upgrade() -> None:
    op.add_column("order_lines", sa.Column("current_quantity", sa.Numeric(20, 6), nullable=True))
    op.add_column("order_lines", _amount("refunded_quantity"))
    _amounts(
        "order_lines",
        "quantity > 0 AND price >= 0 AND current_quantity >= 0 AND refunded_quantity >= 0",
    )
    op.add_column("orders", _amount("refunded"))
    op.add_column("orders", sa.Column("store_customer", sa.Text(), nullable=True))
    op.add_column(
        "orders", sa.Column("store_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    _amounts("orders", "shipping >= 0 AND tax >= 0 AND discount >= 0 AND refunded >= 0")
    op.create_index(
        "ix_orders_store_customer", "orders", ["organization_id", "store_id", "store_customer"]
    )
    op.add_column("store_nudges", sa.Column("body", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("store_nudges", "body")
    op.drop_index("ix_orders_store_customer", "orders")
    _amounts("orders", "shipping >= 0 AND tax >= 0 AND discount >= 0")
    for column in ("store_updated_at", "store_customer", "refunded"):
        op.drop_column("orders", column)
    _amounts("order_lines", "quantity > 0 AND price >= 0")
    for column in ("refunded_quantity", "current_quantity"):
        op.drop_column("order_lines", column)
