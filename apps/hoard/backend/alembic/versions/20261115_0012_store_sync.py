"""Syncing a store: Hoard's copy of each store product, each listing's price,
when the worker took a nudge, and the "synced from store" reason.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, reference, stamp

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json(name: str, empty: str) -> sa.Column:
    return sa.Column(
        name, postgresql.JSONB(), nullable=False, server_default=sa.text(f"'{empty}'::jsonb")
    )


def _waiting(where: str) -> None:
    op.create_index(
        "uq_store_nudges_waiting",
        "store_nudges",
        ["organization_id", "store_id", "topic", "external_id"],
        unique=True,
        postgresql_where=sa.text(where),
    )


def upgrade() -> None:
    op.create_table(
        "store_products",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("store_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("product_type", sa.Text(), nullable=True),
        sa.Column("vendor", sa.Text(), nullable=True),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'")
        ),
        _json("options", "[]"),
        _json("variants", "[]"),
        _json("images", "[]"),
        _json("published_on", "[]"),
        sa.Column("store_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    isolate("store_products")
    stamp("store_products")
    reference("store_products", "store_id", "stores", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_store_products_external",
        "store_products",
        ["organization_id", "store_id", "product_id"],
    )

    op.add_column("listings", sa.Column("price", sa.Numeric(20, 6), nullable=True))

    op.add_column("store_nudges", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_index("uq_store_nudges_waiting", "store_nudges")
    _waiting("done_at IS NULL AND taken_at IS NULL")

    # Every organization that has its default reasons gains the new one.
    op.execute("ALTER TABLE adjustment_reasons NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO adjustment_reasons (organization_id, key) "
        "SELECT DISTINCT organization_id, 'synced_from_store' FROM adjustment_reasons "
        "WHERE key IS NOT NULL ON CONFLICT DO NOTHING"
    )
    op.execute("ALTER TABLE adjustment_reasons FORCE ROW LEVEL SECURITY")


def _change(table: str, statement: str) -> None:
    """Run ``statement`` on ``table``'s rows, in every organization."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(statement)
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    _change(
        "adjustments",
        "UPDATE adjustments SET reason_id = NULL WHERE reason_id IN "
        "(SELECT id FROM adjustment_reasons WHERE key = 'synced_from_store')",
    )
    _change("adjustment_reasons", "DELETE FROM adjustment_reasons WHERE key = 'synced_from_store'")

    _change(
        "store_nudges", "DELETE FROM store_nudges WHERE taken_at IS NOT NULL AND done_at IS NULL"
    )
    op.drop_index("uq_store_nudges_waiting", "store_nudges")
    _waiting("done_at IS NULL")
    op.drop_column("store_nudges", "taken_at")

    op.drop_column("listings", "price")
    op.drop_table("store_products")
