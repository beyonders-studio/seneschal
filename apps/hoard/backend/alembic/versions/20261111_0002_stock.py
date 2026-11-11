"""Stock: units, categories, locations, materials, products and their
variations, lots, stock levels, adjustments and their reasons, purchases and
stocktakes.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.ledger import drop_balance, keep_balance
from seneschal_core.migrations import isolate, referable, reference

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "category_kind": ("material", "product"),
    "item_kind": ("material", "variation"),
    "product_status": ("active", "archived"),
    "adjustment_kind": ("starting", "purchase", "stocktake", "manual"),
}

#: Each table in the order it is made: whether other tables reference it, and
#: its references as (column, parent, on delete).
REFERENCES: dict[str, tuple[bool, list[tuple[str, str, str | None]]]] = {
    "units": (True, []),
    "categories": (True, []),
    "locations": (True, []),
    "items": (True, []),
    "materials": (
        True,
        [
            ("id", "items", "CASCADE"),
            ("tracking_unit_id", "units", None),
            ("purchase_unit_id", "units", None),
            ("category_id", "categories", None),
        ],
    ),
    "products": (True, [("category_id", "categories", None)]),
    "variations": (False, [("id", "items", "CASCADE"), ("product_id", "products", "CASCADE")]),
    "adjustment_reasons": (True, []),
    "purchases": (True, [("location_id", "locations", None)]),
    "purchase_lines": (
        True,
        [("purchase_id", "purchases", "CASCADE"), ("material_id", "materials", None)],
    ),
    "lots": (
        True,
        [
            ("item_id", "items", "CASCADE"),
            ("location_id", "locations", None),
            ("purchase_line_id", "purchase_lines", "CASCADE"),
        ],
    ),
    "stock_levels": (
        False,
        [("item_id", "items", "CASCADE"), ("location_id", "locations", "CASCADE")],
    ),
    "stocktakes": (
        True,
        [("location_id", "locations", None), ("category_id", "categories", None)],
    ),
    "stocktake_rows": (
        True,
        [("stocktake_id", "stocktakes", "CASCADE"), ("item_id", "items", "CASCADE")],
    ),
    "adjustments": (
        False,
        [
            ("item_id", "items", "CASCADE"),
            ("location_id", "locations", None),
            ("lot_id", "lots", None),
            ("reason_id", "adjustment_reasons", None),
            ("purchase_line_id", "purchase_lines", "CASCADE"),
            ("stocktake_row_id", "stocktake_rows", "CASCADE"),
        ],
    ),
}


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _ref(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _amount(name: str, *, default: str | None = "0") -> sa.Column:
    """An exact quantity, cost or price; nullable when it has no default."""
    return sa.Column(name, sa.Numeric(20, 6), nullable=default is None, server_default=default)


def _enum(name: str, column: str, **kwargs) -> sa.Column:
    return sa.Column(
        column, postgresql.ENUM(name=name, create_type=False), nullable=False, **kwargs
    )


def _text(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _flag(name: str, default: bool) -> sa.Column:
    return sa.Column(
        name, sa.Boolean(), nullable=False, server_default=sa.true() if default else sa.false()
    )


def _unique(table: str, name: str, *columns: str) -> None:
    op.create_unique_constraint(name, table, ["organization_id", *columns])


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind)

    tables: dict[str, list] = {
        "units": [_id(), _text("name")],
        "categories": [_id(), _enum("category_kind", "kind"), _text("name")],
        "locations": [
            _id(),
            _text("name", nullable=True),
            _flag("main", False),
            sa.CheckConstraint("name IS NOT NULL OR main", name="ck_locations_name"),
        ],
        "items": [_id(), _enum("item_kind", "kind"), _amount("average_cost")],
        "materials": [
            sa.Column("id", sa.BigInteger(), primary_key=True),
            _text("name"),
            _text("sku", nullable=True),
            _flag("inventoriable", True),
            _ref("tracking_unit_id"),
            _ref("purchase_unit_id", nullable=True),
            _amount("purchase_conversion", default="1"),
            _amount("low_stock_limit", default=None),
            _ref("category_id", nullable=True),
            _ref("supplier_contact_id", nullable=True),
            _text("supplier_code", nullable=True),
            sa.CheckConstraint("purchase_conversion > 0", name="ck_materials_purchase_conversion"),
        ],
        "products": [
            _id(),
            _text("name"),
            _enum("product_status", "status", server_default="active"),
            _ref("category_id", nullable=True),
            _amount("default_price", default=None),
            _amount("low_stock_limit", default=None),
        ],
        "variations": [
            sa.Column("id", sa.BigInteger(), primary_key=True),
            _ref("product_id"),
            sa.Column(
                "attributes",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            _text("sku", nullable=True),
            _amount("price", default=None),
            _flag("is_default", False),
        ],
        "adjustment_reasons": [_id(), _text("name")],
        "purchases": [
            _id(),
            _text("code"),
            sa.Column("dated_on", sa.Date(), nullable=False),
            _ref("supplier_contact_id", nullable=True),
            _ref("location_id"),
            _flag("paid", False),
            _amount("discount"),
            _amount("shipping"),
            _amount("tax"),
            sa.CheckConstraint(
                "discount >= 0 AND shipping >= 0 AND tax >= 0", name="ck_purchases_amounts"
            ),
        ],
        "purchase_lines": [
            _id(),
            _ref("purchase_id"),
            _ref("material_id"),
            _amount("purchase_quantity"),
            _amount("tracking_quantity"),
            _amount("unit_cost"),
            _flag("received", False),
            sa.CheckConstraint(
                "purchase_quantity > 0 AND tracking_quantity > 0 AND unit_cost >= 0",
                name="ck_purchase_lines_amounts",
            ),
        ],
        "lots": [
            _id(),
            _ref("item_id"),
            _ref("location_id"),
            _ref("purchase_line_id", nullable=True),
            _text("number"),
            sa.Column("dated_on", sa.Date(), nullable=False),
            sa.Column("expires_on", sa.Date(), nullable=True),
            _text("notes", nullable=True),
            _amount("balance"),
        ],
        "stock_levels": [_id(), _ref("item_id"), _ref("location_id"), _amount("quantity")],
        "stocktakes": [
            _id(),
            _enum("category_kind", "kind"),
            _ref("location_id"),
            _ref("category_id", nullable=True),
            sa.Column("sample_size", sa.Integer(), nullable=True),
            sa.Column("dated_on", sa.Date(), nullable=False),
            _text("notes", nullable=True),
            sa.CheckConstraint("sample_size > 0", name="ck_stocktakes_sample_size"),
        ],
        "stocktake_rows": [
            _id(),
            _ref("stocktake_id"),
            _ref("item_id"),
            _amount("expected", default=None),
            _amount("counted", default=None),
            sa.CheckConstraint(
                "(expected IS NULL) = (counted IS NULL)", name="ck_stocktake_rows_confirmed"
            ),
        ],
        "adjustments": [
            _id(),
            _ref("item_id"),
            _ref("location_id"),
            _ref("lot_id", nullable=True),
            _enum("adjustment_kind", "kind"),
            sa.Column("dated_on", sa.Date(), nullable=False),
            sa.Column("change", sa.Numeric(20, 6), nullable=False),
            _amount("unit_cost"),
            _ref("reason_id", nullable=True),
            _text("notes", nullable=True),
            _ref("purchase_line_id", nullable=True),
            _ref("stocktake_row_id", nullable=True),
            sa.UniqueConstraint("purchase_line_id", name="uq_adjustments_purchase_line_id"),
            sa.UniqueConstraint("stocktake_row_id", name="uq_adjustments_stocktake_row_id"),
            sa.CheckConstraint("change <> 0", name="ck_adjustments_change"),
            sa.CheckConstraint(
                "(kind = 'purchase') = (purchase_line_id IS NOT NULL)",
                name="ck_adjustments_purchase",
            ),
            sa.CheckConstraint(
                "(kind = 'stocktake') = (stocktake_row_id IS NOT NULL)",
                name="ck_adjustments_stocktake",
            ),
        ],
    }
    for table, (referenced, references) in REFERENCES.items():
        op.create_table(table, *tables[table])
        isolate(table)
        if referenced:
            referable(table)
        for column, parent, ondelete in references:
            reference(table, column, parent, ondelete=ondelete)

    _unique("units", "uq_units_name", "name")
    _unique("categories", "uq_categories_kind_name", "kind", "name")
    _unique("locations", "uq_locations_name", "name")
    op.create_index(
        "uq_locations_main",
        "locations",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("main"),
    )
    op.create_index(
        "uq_variations_default",
        "variations",
        ["organization_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    _unique("adjustment_reasons", "uq_adjustment_reasons_name", "name")
    _unique("purchases", "uq_purchases_code", "code")
    _unique("stock_levels", "uq_stock_levels_item_location", "item_id", "location_id")
    _unique("stocktake_rows", "uq_stocktake_rows_item", "stocktake_id", "item_id")
    op.create_index(
        "ix_adjustments_item", "adjustments", ["organization_id", "item_id", "dated_on", "id"]
    )
    op.create_index("ix_lots_item", "lots", ["organization_id", "item_id", "location_id"])
    op.create_index(
        "ix_purchase_lines_purchase", "purchase_lines", ["organization_id", "purchase_id"]
    )

    keep_balance(
        "adjustments",
        amount="change",
        balances="stock_levels",
        total="quantity",
        keys={"item_id": "item_id", "location_id": "location_id"},
        create=True,
    )
    keep_balance(
        "adjustments",
        amount="change",
        balances="lots",
        total="balance",
        keys={"id": "lot_id"},
        create=False,
    )


def downgrade() -> None:
    drop_balance("adjustments", "lots")
    drop_balance("adjustments", "stock_levels")
    for table in reversed(REFERENCES):
        op.drop_table(table)
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind())
