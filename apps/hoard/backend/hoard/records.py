"""Hoard's records: what it stocks, where, and every change to it.

* **Units** people add, and **categories**, each typed by the kind of record
  it sorts.
* **Locations**: stock and lots sit per location. Each organization has one
  main location, made on its first change to stock (``hoard.stock``).
* **Items**: whatever holds stock. A **material** is bought, never made; a
  **base** is made from materials and other bases, and never bought or sold;
  a **product** is sold, through its **variations**, and a simple product has
  one default variation. Each material, base and variation is an item, and
  the item holds its average unit cost (``hoard.costing``).
* **Recipes**: what one batch of a variation or a base is made from, and how
  much it makes. A product marked a **bundle** has a recipe of its own, whose
  lines are the variations it holds.
* **Production runs** make a variation or a base from a recipe, in batches,
  into the output lot that shares the run's code. Each run keeps its own
  lines of what it consumes, from which lots.
* **Lots**: a number, a date, an expiry and notes, per item and location,
  each with a running balance.
* **Stock levels**: what is on hand of an item at a location.
* **Adjustments**: every change to stock is one, with its kind, date, change,
  the unit cost at the time, a reason, notes, and the record that caused it.
  The database keeps stock levels and lot balances the sums of their
  adjustments (``seneschal_core.ledger``).
* **Purchases** of materials, from a supplier among the community's contacts,
  with lines that are received into lots.
* **Task links**: the Initiative task that tracks a run or a purchase.
* **The event outbox**: events recorded with the change that caused them,
  waiting to be sent to Initiative.
* **Stocktakes**: counts of materials or products at a location, whose
  variances become adjustments.
* **Settings**: the organization's one row of them, such as its currency.
* **Product images**: pictures of a product, which Hoard keeps itself.
* **Properties** people add to a kind of record, in the UI kit's shapes, and
  each record's **values** of them.
* **Layouts**: how a kind of record is listed and shown on its own page, once
  someone changes it from how Hoard ships it, in the UI kit's shapes.

Quantities, costs and prices are exact decimals to six places. A supplier is an
Initiative contact, named by its id, and an initiative and a task are named by
their Initiative ids. Every record says who made it and when, and who last
changed it and when; items, stock levels and the event outbox, which only hold
what is derived from records, do not.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Field

from seneschal_core.models import (
    OrganizationRow,
    Stamped,
    enum_column,
    identity,
    referable,
    reference,
)

#: The places every quantity, cost and price is kept to.
PLACES = Decimal("0.000001")
#: The currency an organization keeps money in until it picks another: an
#: ISO 4217 code.
DEFAULT_CURRENCY = "USD"
#: The types a product image may be.
IMAGE_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
#: The most one product image may hold, in bytes.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: The currencies a money property can be kept in: ISO 4217's codes for the
#: currencies in circulation, as the UI kit offers them.
CURRENCY_CODES = tuple(
    """
    AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BRL BSD BTN BWP BYN
    BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP
    GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS
    KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR
    MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
    RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP
    TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW
    ZWG
    """.split()
)


def amount(default: Decimal | None = Decimal(0)) -> Any:
    """A quantity, cost or price: exact, to six places."""
    return Field(default=default, max_digits=20, decimal_places=6)


class CategoryKind(StrEnum):
    """The kinds of record a category sorts, and a stocktake counts."""

    MATERIAL = "material"
    PRODUCT = "product"
    BASE = "base"


class ItemKind(StrEnum):
    """What an item is."""

    MATERIAL = "material"
    VARIATION = "variation"
    BASE = "base"


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
    #: A completed production run's: each of its lines consumed, and its output made.
    PRODUCTION = "production"


class RecordKind(StrEnum):
    """The kinds of record people add their own properties to and lay out. A
    later revision adds a kind to the ``record_kind`` type, and a member here."""

    MATERIAL = "material"
    PRODUCT = "product"
    LOT = "lot"
    BASE = "base"
    RUN = "run"


class RunStatus(StrEnum):
    """Where a production run stands. Only a completed run has moved stock."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Allergen(StrEnum):
    """The food allergens a material or a lot may contain: the US's nine and
    the EU's fourteen, by one key each where they name the same thing."""

    CELERY = "celery"
    CRUSTACEANS = "crustaceans"
    EGGS = "eggs"
    FISH = "fish"
    #: Cereals containing gluten.
    GLUTEN = "gluten"
    LUPIN = "lupin"
    MILK = "milk"
    MOLLUSCS = "molluscs"
    MUSTARD = "mustard"
    PEANUTS = "peanuts"
    SESAME = "sesame"
    SOYBEANS = "soybeans"
    #: Sulphur dioxide and sulphites.
    SULPHITES = "sulphites"
    TREE_NUTS = "tree_nuts"
    WHEAT = "wheat"


#: The check every allergen list keeps: each of :class:`Allergen`'s keys.
ALLERGENS_CHECK = "allergens <@ ARRAY[{}]::text[]".format(
    ", ".join(f"'{allergen}'" for allergen in Allergen)
)


def allergens() -> Any:
    """Allergen keys, or None where they are not known."""
    return Field(default=None, sa_column=Column(ARRAY(Text), nullable=True))


class PropertyType(StrEnum):
    """What a property holds: the types the UI kit offers people."""

    TEXT = "text"
    NUMBER = "number"
    CHECKBOX = "checkbox"
    DATE = "date"
    DATETIME = "datetime"
    URL = "url"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    #: A member, by their reference.
    USER_REFERENCE = "user_reference"
    #: Money, in the property's currency.
    CURRENCY = "currency"
    #: A figure from 0 to 100.
    SCALE = "scale"
    COUNTER = "counter"
    #: An Initiative file, by its id.
    FILE = "file"
    #: Who signed, the name they typed, and when.
    SIGNATURE = "signature"


class LayoutView(StrEnum):
    """How a layout shows a kind of record: listed in a table, or on its own page."""

    TABLE = "table"
    DETAIL = "detail"


class Unit(OrganizationRow, Stamped, table=True):
    """A measurement unit."""

    __tablename__ = "units"
    __table_args__ = (
        referable("units"),
        UniqueConstraint("organization_id", "name", name="uq_units_name"),
    )

    id: int = identity()
    name: str


class Category(OrganizationRow, Stamped, table=True):
    """A category, typed by the kind of record it sorts."""

    __tablename__ = "categories"
    __table_args__ = (
        referable("categories"),
        UniqueConstraint("organization_id", "kind", "name", name="uq_categories_kind_name"),
    )

    id: int = identity()
    kind: CategoryKind = Field(sa_column=enum_column(CategoryKind, "category_kind"))
    name: str


class Location(OrganizationRow, Stamped, table=True):
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
    #: Whether a material's or a base's stock was at or below its low-stock
    #: limit when ``hoard.events`` last looked.
    low_stock: bool = False


class Material(OrganizationRow, Stamped, table=True):
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
        CheckConstraint(ALLERGENS_CHECK, name="ck_materials_allergens"),
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
    #: What an ingredient list calls it, when not its name.
    ingredient_name: str | None = None
    #: The allergens it contains, as its supplier declares them; None until known.
    allergens: list[str] | None = allergens()


class Base(OrganizationRow, Stamped, table=True):
    """Made from materials and other bases by its recipe, never bought or
    sold: a spice blend, a syrup. A setup may call bases by another word
    (:attr:`OrganizationSettings.base_word`)."""

    __tablename__ = "bases"
    __table_args__ = (
        referable("bases"),
        reference("bases", "id", "items", ondelete="CASCADE"),
        reference("bases", "tracking_unit_id", "units"),
        reference("bases", "category_id", "categories"),
    )

    #: Its item's id.
    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str
    sku: str | None = None
    #: The unit stock is counted in.
    tracking_unit_id: int = Field(sa_type=BigInteger)
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    low_stock_limit: Decimal | None = amount(None)


class Product(OrganizationRow, Stamped, table=True):
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
    #: Sold as the variations its recipe holds.
    bundle: bool = False
    #: Whether its variations' stock together was at or below its low-stock
    #: limit when ``hoard.events`` last looked.
    low_stock: bool = False


class Variation(OrganizationRow, Stamped, table=True):
    """One form of a product, with its own stock and price. A simple product's
    one variation is its default, with no attributes."""

    __tablename__ = "variations"
    __table_args__ = (
        referable("variations"),
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


class DefaultReason(StrEnum):
    """The adjustment reasons an organization starts with, which people rename
    or remove."""

    CORRECTION = "correction"
    DAMAGED = "damaged"
    EXPIRED = "expired"
    LOST = "lost"
    PERSONAL_USE = "personal_use"


class AdjustmentReason(OrganizationRow, Stamped, table=True):
    """Why stock was adjusted by hand. A default reason carries its ``key`` and
    has no name until someone gives it one: pages call it by the translated
    words for its key."""

    __tablename__ = "adjustment_reasons"
    __table_args__ = (
        referable("adjustment_reasons"),
        UniqueConstraint("organization_id", "name", name="uq_adjustment_reasons_name"),
        UniqueConstraint("organization_id", "key", name="uq_adjustment_reasons_key"),
        CheckConstraint("name IS NOT NULL OR key IS NOT NULL", name="ck_adjustment_reasons_name"),
    )

    id: int = identity()
    key: str | None = None
    name: str | None = None


class Purchase(OrganizationRow, Stamped, table=True):
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
    #: The initiative it was opened from; None for one opened before Hoard kept it.
    initiative_id: int | None = Field(default=None, sa_type=BigInteger)


class PurchaseLine(OrganizationRow, Stamped, table=True):
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


class Lot(OrganizationRow, Stamped, table=True):
    """A lot of an item at a location, with its running balance."""

    __tablename__ = "lots"
    __table_args__ = (
        referable("lots"),
        reference("lots", "item_id", "items", ondelete="CASCADE"),
        reference("lots", "location_id", "locations"),
        reference("lots", "purchase_line_id", "purchase_lines", ondelete="CASCADE"),
        Index("ix_lots_item", "organization_id", "item_id", "location_id"),
        CheckConstraint(ALLERGENS_CHECK, name="ck_lots_allergens"),
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
    #: The allergens it contains, as its supplier declares them for this lot;
    #: None where they are not known, and its item's hold.
    allergens: list[str] | None = allergens()


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


class Stocktake(OrganizationRow, Stamped, table=True):
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


class StocktakeRow(OrganizationRow, Stamped, table=True):
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


class Adjustment(OrganizationRow, Stamped, table=True):
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
        reference("adjustments", "production_run_id", "production_runs", ondelete="CASCADE"),
        reference(
            "adjustments", "production_run_line_id", "production_run_lines", ondelete="CASCADE"
        ),
        Index("ix_adjustments_item", "organization_id", "item_id", "dated_on", "id"),
        Index("ix_adjustments_production_run", "organization_id", "production_run_id"),
        Index(
            "uq_adjustments_production_output",
            "production_run_id",
            unique=True,
            postgresql_where=text("production_run_line_id IS NULL"),
        ),
        UniqueConstraint("purchase_line_id", name="uq_adjustments_purchase_line_id"),
        UniqueConstraint("stocktake_row_id", name="uq_adjustments_stocktake_row_id"),
        UniqueConstraint("production_run_line_id", name="uq_adjustments_production_run_line_id"),
        CheckConstraint("change <> 0", name="ck_adjustments_change"),
        CheckConstraint(
            "(kind = 'purchase') = (purchase_line_id IS NOT NULL)",
            name="ck_adjustments_purchase",
        ),
        CheckConstraint(
            "(kind = 'stocktake') = (stocktake_row_id IS NOT NULL)",
            name="ck_adjustments_stocktake",
        ),
        CheckConstraint(
            "(kind = 'production') = (production_run_id IS NOT NULL)",
            name="ck_adjustments_production",
        ),
        CheckConstraint(
            "production_run_line_id IS NULL OR production_run_id IS NOT NULL",
            name="ck_adjustments_production_line",
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
    #: What caused it, by kind: the received purchase line, the stocktake's
    #: row, or the completed production run: one of its lines consumed, or,
    #: with no line, its output made.
    purchase_line_id: int | None = Field(default=None, sa_type=BigInteger)
    stocktake_row_id: int | None = Field(default=None, sa_type=BigInteger)
    production_run_id: int | None = Field(default=None, sa_type=BigInteger)
    production_run_line_id: int | None = Field(default=None, sa_type=BigInteger)


class OrganizationSettings(OrganizationRow, Stamped, table=True):
    """The organization's settings, one row of them. Until it is made, each
    setting is its default."""

    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_settings_organization_id"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_settings_currency"),
    )

    id: int = identity()
    #: The ISO 4217 code every cost and price is in.
    currency: str = DEFAULT_CURRENCY
    #: What the organization calls a base, such as "blend"; pages say the
    #: translated word for a base until someone names it.
    base_word: str | None = None


class ProductImage(OrganizationRow, Stamped, table=True):
    """A picture of a product: one of :data:`IMAGE_TYPES`, of at most
    :data:`MAX_IMAGE_BYTES`."""

    __tablename__ = "product_images"
    __table_args__ = (
        reference("product_images", "product_id", "products", ondelete="CASCADE"),
        CheckConstraint(
            "content_type IN ('image/gif', 'image/jpeg', 'image/png', 'image/webp')",
            name="ck_product_images_content_type",
        ),
        CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 5242880", name="ck_product_images_size"
        ),
        Index("ix_product_images_product", "organization_id", "product_id", "position"),
    )

    id: int = identity()
    product_id: int = Field(sa_type=BigInteger)
    #: Its place among the product's images; the first shows for the product.
    position: int = 0
    content_type: str
    content: bytes = Field(sa_type=LargeBinary)


class PropertyDefinition(OrganizationRow, Stamped, table=True):
    """A property people add to a kind of record. A select's ``options``, a
    money property's ``currency`` and a counter's ``counter`` settings are in
    the UI kit's shapes; every other type has none."""

    __tablename__ = "property_definitions"
    __table_args__ = (
        referable("property_definitions"),
        UniqueConstraint("organization_id", "kind", "name", name="uq_property_definitions_name"),
        UniqueConstraint("organization_id", "kind", "key", name="uq_property_definitions_key"),
        CheckConstraint(
            "(type = 'currency') = (currency IS NOT NULL)",
            name="ck_property_definitions_currency",
        ),
    )

    id: int = identity()
    kind: RecordKind = Field(sa_column=enum_column(RecordKind, "record_kind"))
    #: Names a property a starting setup added, so adding it again finds it.
    key: str | None = None
    name: str
    type: PropertyType = Field(sa_column=enum_column(PropertyType, "property_type"))
    options: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSONB))
    currency: str | None = None
    counter: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))


class PropertyValue(OrganizationRow, Stamped, table=True):
    """What one record holds of a property: a material, a product, a lot, a
    base or a production run, whichever its definition's kind is. A record
    holding nothing of it has no row."""

    __tablename__ = "property_values"
    __table_args__ = (
        reference("property_values", "definition_id", "property_definitions", ondelete="CASCADE"),
        reference("property_values", "material_id", "materials", ondelete="CASCADE"),
        reference("property_values", "product_id", "products", ondelete="CASCADE"),
        reference("property_values", "lot_id", "lots", ondelete="CASCADE"),
        reference("property_values", "base_id", "bases", ondelete="CASCADE"),
        reference("property_values", "run_id", "production_runs", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "material_id", "definition_id", name="uq_property_values_material"
        ),
        UniqueConstraint(
            "organization_id", "product_id", "definition_id", name="uq_property_values_product"
        ),
        UniqueConstraint(
            "organization_id", "lot_id", "definition_id", name="uq_property_values_lot"
        ),
        UniqueConstraint(
            "organization_id", "base_id", "definition_id", name="uq_property_values_base"
        ),
        UniqueConstraint(
            "organization_id", "run_id", "definition_id", name="uq_property_values_run"
        ),
        CheckConstraint(
            "num_nonnulls(material_id, product_id, lot_id, base_id, run_id) = 1",
            name="ck_property_values_record",
        ),
    )

    id: int = identity()
    definition_id: int = Field(sa_type=BigInteger)
    material_id: int | None = Field(default=None, sa_type=BigInteger)
    product_id: int | None = Field(default=None, sa_type=BigInteger)
    lot_id: int | None = Field(default=None, sa_type=BigInteger)
    base_id: int | None = Field(default=None, sa_type=BigInteger)
    run_id: int | None = Field(default=None, sa_type=BigInteger)
    value: Any = Field(sa_column=Column(JSONB, nullable=False))


class Layout(OrganizationRow, Stamped, table=True):
    """How a kind of record is listed or shown on its own page, in the UI
    kit's layout shapes. Until it is changed it has no row, and pages draw it
    as Hoard ships it."""

    __tablename__ = "layouts"
    __table_args__ = (UniqueConstraint("organization_id", "kind", "view", name="uq_layouts_view"),)

    id: int = identity()
    kind: RecordKind = Field(sa_column=enum_column(RecordKind, "record_kind"))
    view: LayoutView = Field(sa_column=enum_column(LayoutView, "layout_view"))
    definition: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))


class Recipe(OrganizationRow, Stamped, table=True):
    """What one batch of a variation or a base is made from, and how much of
    it a batch makes; or what a bundle product holds. Each has at most one."""

    __tablename__ = "recipes"
    __table_args__ = (
        referable("recipes"),
        reference("recipes", "variation_id", "variations", ondelete="CASCADE"),
        reference("recipes", "base_id", "bases", ondelete="CASCADE"),
        reference("recipes", "product_id", "products", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "variation_id", name="uq_recipes_variation"),
        UniqueConstraint("organization_id", "base_id", name="uq_recipes_base"),
        UniqueConstraint("organization_id", "product_id", name="uq_recipes_product"),
        CheckConstraint(
            "num_nonnulls(variation_id, base_id, product_id) = 1", name="ck_recipes_output"
        ),
        CheckConstraint(
            "batch_quantity > 0 AND (minutes IS NULL OR minutes >= 0)", name="ck_recipes_batch"
        ),
    )

    id: int = identity()
    variation_id: int | None = Field(default=None, sa_type=BigInteger)
    base_id: int | None = Field(default=None, sa_type=BigInteger)
    #: A bundle product, whose lines are the variations it holds.
    product_id: int | None = Field(default=None, sa_type=BigInteger)
    #: How much one batch makes, in its output's unit.
    batch_quantity: Decimal = amount(Decimal(1))
    #: How long one batch takes.
    minutes: int | None = None


class RecipeLine(OrganizationRow, Stamped, table=True):
    """One item a batch takes, and how much of it, in its tracking unit: a
    material or a base, or, in a bundle's recipe, a variation."""

    __tablename__ = "recipe_lines"
    __table_args__ = (
        reference("recipe_lines", "recipe_id", "recipes", ondelete="CASCADE"),
        reference("recipe_lines", "item_id", "items"),
        UniqueConstraint("organization_id", "recipe_id", "item_id", name="uq_recipe_lines_item"),
        CheckConstraint("quantity > 0", name="ck_recipe_lines_quantity"),
    )

    id: int = identity()
    recipe_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    quantity: Decimal = amount()
    #: Its place among the recipe's lines.
    position: int = 0


class ProductionRun(OrganizationRow, Stamped, table=True):
    """Making a variation or a base at a location, in batches of its recipe.
    Its code is its output lot's number. Only completing it moves stock: each
    line out, and the actual quantity in, into its output lot."""

    __tablename__ = "production_runs"
    __table_args__ = (
        referable("production_runs"),
        reference("production_runs", "item_id", "items", ondelete="CASCADE"),
        reference("production_runs", "recipe_id", "recipes"),
        reference("production_runs", "location_id", "locations"),
        reference("production_runs", "output_lot_id", "lots"),
        UniqueConstraint("organization_id", "code", name="uq_production_runs_code"),
        CheckConstraint(
            "planned_batches > 0 AND actual_quantity >= 0 AND labour_minutes >= 0",
            name="ck_production_runs_amounts",
        ),
        CheckConstraint(
            "status <> 'completed' OR (completed_on IS NOT NULL AND actual_quantity IS NOT NULL)",
            name="ck_production_runs_completed",
        ),
    )

    id: int = identity()
    code: str
    #: What it makes: a variation or a base.
    item_id: int = Field(sa_type=BigInteger)
    #: The recipe its lines were filled from; None once that recipe is deleted.
    recipe_id: int | None = Field(default=None, sa_type=BigInteger)
    status: RunStatus = Field(
        default=RunStatus.NOT_STARTED,
        sa_column=enum_column(RunStatus, "run_status", server_default="not_started"),
    )
    location_id: int = Field(sa_type=BigInteger)
    #: The initiative it was opened from.
    initiative_id: int | None = Field(default=None, sa_type=BigInteger)
    planned_batches: Decimal = amount(Decimal(1))
    #: How much it made, in its output's unit: known once it is completed.
    actual_quantity: Decimal | None = amount(None)
    labour_minutes: int | None = None
    #: Who is making it: a member's reference.
    assignee_ref: str | None = None
    started_on: date | None = None
    deadline_on: date | None = None
    completed_on: date | None = None
    #: When what it made expires.
    expires_on: date | None = None
    #: The lot it made, once it is completed.
    output_lot_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Whether a material it consumed had no cost on its date, which
    #: ``hoard.costing`` keeps.
    uncosted: bool = False


class ProductionRunLine(OrganizationRow, Stamped, table=True):
    """What a run consumes of one item, in its tracking unit, and from which
    lot: the lot someone recorded, or the one Hoard assumed (``assumed``)."""

    __tablename__ = "production_run_lines"
    __table_args__ = (
        referable("production_run_lines"),
        reference("production_run_lines", "run_id", "production_runs", ondelete="CASCADE"),
        reference("production_run_lines", "item_id", "items"),
        reference("production_run_lines", "lot_id", "lots"),
        Index("ix_production_run_lines_run", "organization_id", "run_id"),
        CheckConstraint("quantity > 0", name="ck_production_run_lines_quantity"),
    )

    id: int = identity()
    run_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    lot_id: int | None = Field(default=None, sa_type=BigInteger)
    quantity: Decimal = amount()
    assumed: bool = False


class TaskLink(OrganizationRow, Stamped, table=True):
    """The Initiative task that tracks a production run or a purchase: one
    task each, and one record per task."""

    __tablename__ = "task_links"
    __table_args__ = (
        reference("task_links", "run_id", "production_runs", ondelete="CASCADE"),
        reference("task_links", "purchase_id", "purchases", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "task_id", name="uq_task_links_task"),
        UniqueConstraint("organization_id", "run_id", name="uq_task_links_run"),
        UniqueConstraint("organization_id", "purchase_id", name="uq_task_links_purchase"),
        CheckConstraint("num_nonnulls(run_id, purchase_id) = 1", name="ck_task_links_record"),
    )

    id: int = identity()
    #: The task's Initiative id.
    task_id: int = Field(sa_type=BigInteger)
    run_id: int | None = Field(default=None, sa_type=BigInteger)
    purchase_id: int | None = Field(default=None, sa_type=BigInteger)


class OutboxEvent(OrganizationRow, table=True):
    """An event recorded with the change that caused it, waiting to be sent to
    Initiative (``hoard.events``). It is deleted once sent."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        Index("ix_event_outbox_due", "organization_id", "next_attempt_at"),
        CheckConstraint("attempts >= 0", name="ck_event_outbox_attempts"),
    )

    id: int = identity()
    #: The event's name, such as ``run-started``.
    name: str
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    #: The initiative it is about, if it is about one.
    initiative_id: int | None = Field(default=None, sa_type=BigInteger)
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    attempts: int = 0
    #: When to send it next.
    next_attempt_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
