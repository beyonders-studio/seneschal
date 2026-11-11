"""Hoard's records: what it stocks, where, and every change to it.

* **Units** people add, and **categories**, each typed by the kind of record
  it sorts.
* **Locations**: stock and lots sit per location. Each organization has one
  main location, made on its first change to stock (``hoard.stock``).
* **Items**: whatever holds stock. A **material** is bought, never made; a
  **product** is sold, through its **variations**, and a simple product has
  one default variation. Each material and variation is an item, and the item
  holds its average unit cost (``hoard.costing``).
* **Lots**: a number, a date, an expiry and notes, per item and location,
  each with a running balance.
* **Stock levels**: what is on hand of an item at a location.
* **Adjustments**: every change to stock is one, with its kind, date, change,
  the unit cost at the time, a reason, notes, and the record that caused it.
  The database keeps stock levels and lot balances the sums of their
  adjustments (``seneschal_core.ledger``).
* **Purchases** of materials, from a supplier among the community's contacts,
  with lines that are received into lots.
* **Stocktakes**: counts of materials or products at a location, whose
  variances become adjustments.

Quantities, costs and prices are exact decimals to six places. A supplier is an
Initiative contact, named by its id.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from seneschal_core.models import OrganizationRow, enum_column, identity, referable, reference

#: The places every quantity, cost and price is kept to.
PLACES = Decimal("0.000001")


def amount(default: Decimal | None = Decimal(0)) -> Any:
    """A quantity, cost or price: exact, to six places."""
    return Field(default=default, max_digits=20, decimal_places=6)


class CategoryKind(StrEnum):
    """The kinds of record a category sorts, and a stocktake counts."""

    MATERIAL = "material"
    PRODUCT = "product"


class ItemKind(StrEnum):
    """What an item is."""

    MATERIAL = "material"
    VARIATION = "variation"


class ProductStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AdjustmentKind(StrEnum):
    """What made a change to stock. A later revision adds a kind to the
    ``adjustment_kind`` type, and a member here."""

    #: Stock an item started with, at a cost typed in.
    STARTING = "starting"
    #: A received purchase line.
    PURCHASE = "purchase"
    #: A stocktake's variance.
    STOCKTAKE = "stocktake"
    #: A change made by hand, with its reason.
    MANUAL = "manual"


class Unit(OrganizationRow, table=True):
    """A measurement unit."""

    __tablename__ = "units"
    __table_args__ = (
        referable("units"),
        UniqueConstraint("organization_id", "name", name="uq_units_name"),
    )

    id: int = identity()
    name: str


class Category(OrganizationRow, table=True):
    """A category, typed by the kind of record it sorts."""

    __tablename__ = "categories"
    __table_args__ = (
        referable("categories"),
        UniqueConstraint("organization_id", "kind", "name", name="uq_categories_kind_name"),
    )

    id: int = identity()
    kind: CategoryKind = Field(sa_column=enum_column(CategoryKind, "category_kind"))
    name: str


class Location(OrganizationRow, table=True):
    """A place stock sits. ``main`` marks the one every change defaults to,
    which has no name until someone gives it one: pages call it by the
    translated word for the main location."""

    __tablename__ = "locations"
    __table_args__ = (
        referable("locations"),
        UniqueConstraint("organization_id", "name", name="uq_locations_name"),
        Index(
            "uq_locations_main",
            "organization_id",
            unique=True,
            postgresql_where=text("main"),
        ),
        CheckConstraint("name IS NOT NULL OR main", name="ck_locations_name"),
    )

    id: int = identity()
    name: str | None = None
    main: bool = False


class Item(OrganizationRow, table=True):
    """Whatever holds stock: a material or a variation, which shares its id."""

    __tablename__ = "items"
    __table_args__ = (referable("items"),)

    id: int = identity()
    kind: ItemKind = Field(sa_column=enum_column(ItemKind, "item_kind"))
    #: The moving weighted average unit cost, which ``hoard.costing`` derives.
    average_cost: Decimal = amount()


class Material(OrganizationRow, table=True):
    """Bought, never made. A material that is not inventoriable, such as a
    tool, carries no stock."""

    __tablename__ = "materials"
    __table_args__ = (
        referable("materials"),
        reference("materials", "id", "items", ondelete="CASCADE"),
        reference("materials", "tracking_unit_id", "units"),
        reference("materials", "purchase_unit_id", "units"),
        reference("materials", "category_id", "categories"),
        CheckConstraint("purchase_conversion > 0", name="ck_materials_purchase_conversion"),
    )

    #: Its item's id.
    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str
    sku: str | None = None
    inventoriable: bool = True
    #: The unit stock is counted in.
    tracking_unit_id: int = Field(sa_type=BigInteger)
    #: The unit it is bought in, when not the tracking unit.
    purchase_unit_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Tracking units in one purchase unit.
    purchase_conversion: Decimal = amount(Decimal(1))
    low_stock_limit: Decimal | None = amount(None)
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The preferred supplier: an Initiative contact's id.
    supplier_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The supplier's code for it.
    supplier_code: str | None = None


class Product(OrganizationRow, table=True):
    """Sold, through its variations."""

    __tablename__ = "products"
    __table_args__ = (
        referable("products"),
        reference("products", "category_id", "categories"),
    )

    id: int = identity()
    name: str
    status: ProductStatus = Field(
        default=ProductStatus.ACTIVE,
        sa_column=enum_column(ProductStatus, "product_status", server_default="active"),
    )
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    default_price: Decimal | None = amount(None)
    low_stock_limit: Decimal | None = amount(None)


class Variation(OrganizationRow, table=True):
    """One form of a product, with its own stock and price. A simple product's
    one variation is its default, with no attributes."""

    __tablename__ = "variations"
    __table_args__ = (
        reference("variations", "id", "items", ondelete="CASCADE"),
        reference("variations", "product_id", "products", ondelete="CASCADE"),
        Index(
            "uq_variations_default",
            "organization_id",
            "product_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    #: Its item's id.
    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    product_id: int = Field(sa_type=BigInteger)
    #: Each attribute's value, such as ``{"size": "large"}``.
    attributes: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    sku: str | None = None
    #: Its price, when not the product's default price.
    price: Decimal | None = amount(None)
    is_default: bool = False


class AdjustmentReason(OrganizationRow, table=True):
    """Why stock was adjusted by hand, as people name it."""

    __tablename__ = "adjustment_reasons"
    __table_args__ = (
        referable("adjustment_reasons"),
        UniqueConstraint("organization_id", "name", name="uq_adjustment_reasons_name"),
    )

    id: int = identity()
    name: str


class Purchase(OrganizationRow, table=True):
    """Materials bought from a supplier, received at a location. Its discount
    and shipping are spread over its lines by value; its tax is not a cost."""

    __tablename__ = "purchases"
    __table_args__ = (
        referable("purchases"),
        reference("purchases", "location_id", "locations"),
        UniqueConstraint("organization_id", "code", name="uq_purchases_code"),
        CheckConstraint(
            "discount >= 0 AND shipping >= 0 AND tax >= 0", name="ck_purchases_amounts"
        ),
    )

    id: int = identity()
    code: str
    dated_on: date
    #: The supplier: an Initiative contact's id.
    supplier_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    paid: bool = False
    discount: Decimal = amount()
    shipping: Decimal = amount()
    tax: Decimal = amount()


class PurchaseLine(OrganizationRow, table=True):
    """A material bought: so many purchase units at a unit cost each, which
    are so many tracking units. Receiving it makes its lot and its purchase
    adjustment."""

    __tablename__ = "purchase_lines"
    __table_args__ = (
        referable("purchase_lines"),
        reference("purchase_lines", "purchase_id", "purchases", ondelete="CASCADE"),
        reference("purchase_lines", "material_id", "materials"),
        Index("ix_purchase_lines_purchase", "organization_id", "purchase_id"),
        CheckConstraint(
            "purchase_quantity > 0 AND tracking_quantity > 0 AND unit_cost >= 0",
            name="ck_purchase_lines_amounts",
        ),
    )

    id: int = identity()
    purchase_id: int = Field(sa_type=BigInteger)
    material_id: int = Field(sa_type=BigInteger)
    purchase_quantity: Decimal = amount()
    tracking_quantity: Decimal = amount()
    #: The cost of one purchase unit, before discount and shipping.
    unit_cost: Decimal = amount()
    received: bool = False


class Lot(OrganizationRow, table=True):
    """A lot of an item at a location, with its running balance."""

    __tablename__ = "lots"
    __table_args__ = (
        referable("lots"),
        reference("lots", "item_id", "items", ondelete="CASCADE"),
        reference("lots", "location_id", "locations"),
        reference("lots", "purchase_line_id", "purchase_lines", ondelete="CASCADE"),
        Index("ix_lots_item", "organization_id", "item_id", "location_id"),
    )

    id: int = identity()
    item_id: int = Field(sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    #: The purchase line that received it.
    purchase_line_id: int | None = Field(default=None, sa_type=BigInteger)
    number: str
    dated_on: date
    expires_on: date | None = None
    notes: str | None = None
    #: The sum of its adjustments, which the database keeps.
    balance: Decimal = amount()


class StockLevel(OrganizationRow, table=True):
    """What is on hand of an item at a location: the sum of its adjustments,
    which the database keeps."""

    __tablename__ = "stock_levels"
    __table_args__ = (
        reference("stock_levels", "item_id", "items", ondelete="CASCADE"),
        reference("stock_levels", "location_id", "locations", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "item_id", "location_id", name="uq_stock_levels_item_location"
        ),
    )

    id: int = identity()
    item_id: int = Field(sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    quantity: Decimal = amount()


class Stocktake(OrganizationRow, table=True):
    """A count of materials or products at a location: all of them, or a
    random sample, of one category or every one."""

    __tablename__ = "stocktakes"
    __table_args__ = (
        referable("stocktakes"),
        reference("stocktakes", "location_id", "locations"),
        reference("stocktakes", "category_id", "categories"),
        CheckConstraint("sample_size > 0", name="ck_stocktakes_sample_size"),
    )

    id: int = identity()
    kind: CategoryKind = Field(sa_column=enum_column(CategoryKind, "category_kind"))
    location_id: int = Field(sa_type=BigInteger)
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    #: How many items it samples at random; every one when None.
    sample_size: int | None = None
    dated_on: date
    notes: str | None = None


class StocktakeRow(OrganizationRow, table=True):
    """One item to count. Confirming it records what was on hand and what was
    counted; their difference is its variance."""

    __tablename__ = "stocktake_rows"
    __table_args__ = (
        referable("stocktake_rows"),
        reference("stocktake_rows", "stocktake_id", "stocktakes", ondelete="CASCADE"),
        reference("stocktake_rows", "item_id", "items", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "stocktake_id", "item_id", name="uq_stocktake_rows_item"
        ),
        CheckConstraint(
            "(expected IS NULL) = (counted IS NULL)", name="ck_stocktake_rows_confirmed"
        ),
    )

    id: int = identity()
    stocktake_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    #: On hand when first confirmed.
    expected: Decimal | None = amount(None)
    counted: Decimal | None = amount(None)


class Adjustment(OrganizationRow, table=True):
    """A change to an item's stock at a location, and to a lot's balance when
    it names one. ``unit_cost`` is what one unit cost at the time: typed in for
    a starting adjustment, the landed cost for a purchase, and otherwise the
    moving average (``hoard.costing``)."""

    __tablename__ = "adjustments"
    __table_args__ = (
        reference("adjustments", "item_id", "items", ondelete="CASCADE"),
        reference("adjustments", "location_id", "locations"),
        reference("adjustments", "lot_id", "lots"),
        reference("adjustments", "reason_id", "adjustment_reasons"),
        reference("adjustments", "purchase_line_id", "purchase_lines", ondelete="CASCADE"),
        reference("adjustments", "stocktake_row_id", "stocktake_rows", ondelete="CASCADE"),
        Index("ix_adjustments_item", "organization_id", "item_id", "dated_on", "id"),
        UniqueConstraint("purchase_line_id", name="uq_adjustments_purchase_line_id"),
        UniqueConstraint("stocktake_row_id", name="uq_adjustments_stocktake_row_id"),
        CheckConstraint("change <> 0", name="ck_adjustments_change"),
        CheckConstraint(
            "(kind = 'purchase') = (purchase_line_id IS NOT NULL)",
            name="ck_adjustments_purchase",
        ),
        CheckConstraint(
            "(kind = 'stocktake') = (stocktake_row_id IS NOT NULL)",
            name="ck_adjustments_stocktake",
        ),
    )

    id: int = identity()
    item_id: int = Field(sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    lot_id: int | None = Field(default=None, sa_type=BigInteger)
    kind: AdjustmentKind = Field(sa_column=enum_column(AdjustmentKind, "adjustment_kind"))
    dated_on: date
    change: Decimal = amount()
    unit_cost: Decimal = amount()
    reason_id: int | None = Field(default=None, sa_type=BigInteger)
    notes: str | None = None
    #: What caused it, by kind: the received purchase line, or the stocktake's row.
    purchase_line_id: int | None = Field(default=None, sa_type=BigInteger)
    stocktake_row_id: int | None = Field(default=None, sa_type=BigInteger)
