"""Orders and sales channels: stores, their locations, listings, stock pushes
and the nudges their webhooks leave; orders, their lines, shipments and
returns, with the adjustments they make; whether a store order makes what it
sells; and the "returned damaged" reason.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, quote, referable, reference, stamp

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Each new type and its values.
ENUMS = {
    "channel": ("shopify", "woocommerce"),
    "auto_manufacture": ("off", "always", "out_of_stock"),
    "order_status": ("pending", "open", "shipped", "cancelled"),
    "shipment_status": ("pending", "shipped", "cancelled"),
    "push_status": ("queued", "pushed", "failed"),
    "matched_by": ("sku", "title", "external_id", "hand"),
}

#: Each new table in the order it is made: whether it says who made and
#: changed its records, and whether other tables reference it.
TABLES = {
    "stores": (True, True),
    "store_locations": (True, True),
    "listings": (True, True),
    "stock_pushes": (False, False),
    "store_nudges": (False, False),
    "orders": (True, True),
    "order_lines": (True, True),
    "shipments": (True, True),
    "shipment_lines": (True, True),
    "return_lines": (True, True),
}


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _ref(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _amount(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(20, 6), nullable=False, server_default="0")


def _flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def _text(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _moment(name: str, *, now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=not now,
        server_default=sa.func.now() if now else None,
    )


def _enum(name: str, column: str | None = None, **kwargs) -> sa.Column:
    return sa.Column(column or name, postgresql.ENUM(name=name, create_type=False), **kwargs)


def _set_null(table: str, column: str, parent: str) -> None:
    """``table.column`` references a row of ``parent`` in the same
    organization, and is cleared when that row is deleted."""
    op.execute(
        f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(f'fk_{table}_{column}')} "
        f"FOREIGN KEY (organization_id, {quote(column)}) "
        f"REFERENCES {quote(parent)} (organization_id, id) ON DELETE SET NULL ({quote(column)})"
    )


def _index(table: str, name: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_{name}", table, ["organization_id", *columns])


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in ("shipment", "return"):
            op.execute(f"ALTER TYPE adjustment_kind ADD VALUE IF NOT EXISTS '{value}'")
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind())

    columns: dict[str, list] = {
        "stores": [
            _id(),
            _enum("channel", nullable=False),
            _text("shop_domain"),
            _text("account_label"),
            _flag("connected"),
            _text("last_check_error"),
            _moment("orders_since"),
            _moment("products_since"),
            _moment("imported_at"),
        ],
        "store_locations": [
            _id(),
            _ref("store_id"),
            _text("external_id", nullable=False),
            _text("name"),
            _ref("location_id", nullable=True),
        ],
        "listings": [
            _id(),
            _ref("store_id"),
            _ref("item_id", nullable=True),
            _text("product_id", nullable=False),
            _text("variant_id"),
            _text("inventory_item_id"),
            _text("sku"),
            _text("title"),
            _enum("matched_by", nullable=True),
            sa.CheckConstraint(
                "(item_id IS NULL) = (matched_by IS NULL)", name="ck_listings_matched"
            ),
        ],
        "stock_pushes": [
            _id(),
            _ref("listing_id"),
            _ref("store_location_id", nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("compare_quantity", sa.Integer(), nullable=True),
            _text("idempotency_key", nullable=False),
            _enum("push_status", "status", nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            _moment("next_attempt_at", now=True),
            _text("error"),
            _moment("created_at", now=True),
            _moment("pushed_at"),
            sa.CheckConstraint("attempts >= 0", name="ck_stock_pushes_attempts"),
        ],
        "store_nudges": [
            _id(),
            _ref("store_id"),
            _text("topic", nullable=False),
            _text("external_id", nullable=False),
            _moment("created_at", now=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            _moment("next_attempt_at", now=True),
            _moment("done_at"),
            sa.CheckConstraint("attempts >= 0", name="ck_store_nudges_attempts"),
        ],
        "orders": [
            _id(),
            _text("code", nullable=False),
            _ref("store_id", nullable=True),
            _text("external_id"),
            _text("external_number"),
            _ref("customer_contact_id", nullable=True),
            _ref("location_id"),
            _ref("initiative_id", nullable=True),
            sa.Column("ordered_on", sa.Date(), nullable=False),
            sa.Column("due_on", sa.Date(), nullable=True),
            _flag("paid"),
            _enum("order_status", "status", nullable=False, server_default="open"),
            _amount("shipping"),
            _amount("tax"),
            _amount("discount"),
            _text("delivery_address"),
            sa.CheckConstraint(
                "(store_id IS NULL) = (external_id IS NULL)", name="ck_orders_external"
            ),
            sa.CheckConstraint(
                "store_id IS NOT NULL OR customer_contact_id IS NOT NULL",
                name="ck_orders_customer",
            ),
            sa.CheckConstraint(
                "shipping >= 0 AND tax >= 0 AND discount >= 0", name="ck_orders_amounts"
            ),
        ],
        "order_lines": [
            _id(),
            _ref("order_id"),
            _ref("item_id"),
            _amount("quantity"),
            _amount("price"),
            _text("external_line_id"),
            _ref("bundle_line_id", nullable=True),
            _ref("production_run_id", nullable=True),
            _text("selling_plan"),
            sa.CheckConstraint("quantity > 0 AND price >= 0", name="ck_order_lines_amounts"),
        ],
        "shipments": [
            _id(),
            _ref("order_id"),
            _ref("location_id"),
            _text("external_id"),
            _enum("shipment_status", "status", nullable=False, server_default="pending"),
            sa.Column("shipped_on", sa.Date(), nullable=True),
            _ref("carrier_contact_id", nullable=True),
            _text("tracking"),
            _text("delivery_address"),
            _text("reference"),
            sa.CheckConstraint(
                "status <> 'shipped' OR shipped_on IS NOT NULL", name="ck_shipments_shipped"
            ),
        ],
        "shipment_lines": [
            _id(),
            _ref("shipment_id"),
            _ref("order_line_id"),
            _ref("item_id"),
            _ref("lot_id", nullable=True),
            _amount("quantity"),
            _flag("assumed"),
            sa.CheckConstraint("quantity > 0", name="ck_shipment_lines_quantity"),
        ],
        "return_lines": [
            _id(),
            _ref("shipment_line_id"),
            _amount("quantity"),
            sa.Column("dated_on", sa.Date(), nullable=False),
            _flag("damaged"),
            sa.CheckConstraint("quantity > 0", name="ck_return_lines_quantity"),
        ],
    }
    for table, (stamped, referenced) in TABLES.items():
        op.create_table(table, *columns[table])
        isolate(table)
        if stamped:
            stamp(table)
        if referenced:
            referable(table)

    op.create_unique_constraint("uq_stores_channel", "stores", ["organization_id", "channel"])

    reference("store_locations", "store_id", "stores", ondelete="CASCADE")
    _set_null("store_locations", "location_id", "locations")
    op.create_unique_constraint(
        "uq_store_locations_external",
        "store_locations",
        ["organization_id", "store_id", "external_id"],
    )

    reference("listings", "store_id", "stores", ondelete="CASCADE")
    _set_null("listings", "item_id", "items")
    # A store's product without variants has no variant id, and is listed once too.
    op.execute(
        "ALTER TABLE listings ADD CONSTRAINT uq_listings_external UNIQUE NULLS NOT DISTINCT "
        "(organization_id, store_id, product_id, variant_id)"
    )
    _index("listings", "item", "item_id")

    reference("stock_pushes", "listing_id", "listings", ondelete="CASCADE")
    reference("stock_pushes", "store_location_id", "store_locations", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_stock_pushes_key", "stock_pushes", ["organization_id", "idempotency_key"]
    )
    _index("stock_pushes", "due", "status", "next_attempt_at")
    _index("stock_pushes", "listing", "listing_id", "created_at")

    reference("store_nudges", "store_id", "stores", ondelete="CASCADE")
    op.create_index(
        "uq_store_nudges_waiting",
        "store_nudges",
        ["organization_id", "store_id", "topic", "external_id"],
        unique=True,
        postgresql_where=sa.text("done_at IS NULL"),
    )
    _index("store_nudges", "due", "done_at", "next_attempt_at")

    reference("orders", "store_id", "stores")
    reference("orders", "location_id", "locations")
    op.create_unique_constraint("uq_orders_code", "orders", ["organization_id", "code"])
    op.create_unique_constraint(
        "uq_orders_external", "orders", ["organization_id", "store_id", "external_id"]
    )
    _index("orders", "status", "status")

    reference("order_lines", "order_id", "orders", ondelete="CASCADE")
    reference("order_lines", "item_id", "items")
    reference("order_lines", "bundle_line_id", "order_lines", ondelete="CASCADE")
    _set_null("order_lines", "production_run_id", "production_runs")
    _index("order_lines", "order", "order_id")
    _index("order_lines", "item", "item_id")

    reference("shipments", "order_id", "orders", ondelete="CASCADE")
    reference("shipments", "location_id", "locations")
    op.create_unique_constraint(
        "uq_shipments_external", "shipments", ["organization_id", "order_id", "external_id"]
    )
    _index("shipments", "order", "order_id")

    reference("shipment_lines", "shipment_id", "shipments", ondelete="CASCADE")
    reference("shipment_lines", "order_line_id", "order_lines", ondelete="CASCADE")
    reference("shipment_lines", "item_id", "items")
    _set_null("shipment_lines", "lot_id", "lots")
    _index("shipment_lines", "shipment", "shipment_id")
    _index("shipment_lines", "order_line", "order_line_id")

    reference("return_lines", "shipment_line_id", "shipment_lines", ondelete="CASCADE")
    _index("return_lines", "shipment_line", "shipment_line_id")

    for column, parent in (
        ("shipment_line_id", "shipment_lines"),
        ("return_line_id", "return_lines"),
    ):
        op.add_column("adjustments", _ref(column, nullable=True))
        reference("adjustments", column, parent, ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_adjustments_shipment_line_id", "adjustments", ["shipment_line_id"]
    )
    op.create_unique_constraint(
        "uq_adjustments_return_line_kind", "adjustments", ["return_line_id", "kind"]
    )
    op.create_check_constraint(
        "ck_adjustments_shipment",
        "adjustments",
        "(kind = 'shipment') = (shipment_line_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_adjustments_return",
        "adjustments",
        "(kind <> 'return' OR return_line_id IS NOT NULL) "
        "AND (return_line_id IS NULL OR kind IN ('return', 'manual'))",
    )

    op.add_column("settings", _enum("auto_manufacture", nullable=False, server_default="off"))
    for table in ("products", "variations"):
        op.add_column(table, _enum("auto_manufacture", nullable=True))

    # Every organization that has its default reasons gains the new one; one
    # that has none yet starts with all of them.
    op.execute("ALTER TABLE adjustment_reasons NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO adjustment_reasons (organization_id, key) "
        "SELECT DISTINCT organization_id, 'returned_damaged' FROM adjustment_reasons "
        "WHERE key IS NOT NULL ON CONFLICT DO NOTHING"
    )
    op.execute("ALTER TABLE adjustment_reasons FORCE ROW LEVEL SECURITY")


def _change(table: str, statement: str) -> None:
    """Run ``statement`` on ``table``'s rows, in every organization."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(statement)
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Postgres keeps a type's values once added: the rows holding the new ones
    # go, and the reason added here with what names it.
    _change(
        "adjustments",
        "DELETE FROM adjustments WHERE kind IN ('shipment', 'return') "
        "OR return_line_id IS NOT NULL",
    )
    _change(
        "adjustments",
        "UPDATE adjustments SET reason_id = NULL WHERE reason_id IN "
        "(SELECT id FROM adjustment_reasons WHERE key = 'returned_damaged')",
    )
    _change("adjustment_reasons", "DELETE FROM adjustment_reasons WHERE key = 'returned_damaged'")

    for table in ("variations", "products", "settings"):
        op.drop_column(table, "auto_manufacture")
    op.drop_constraint("ck_adjustments_return", "adjustments")
    op.drop_constraint("ck_adjustments_shipment", "adjustments")
    op.drop_column("adjustments", "return_line_id")
    op.drop_column("adjustments", "shipment_line_id")

    for table in reversed(TABLES):
        op.drop_table(table)
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind())
