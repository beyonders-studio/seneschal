"""Hoard's records: what it stocks, where, and every change to it.

* **Units** people add, and **categories** and **item types**, each typed by
  the kind of record it sorts or shapes: a category for sorting, stocktakes
  and reports, a type for the page a record is shown on.
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
* **Transfers**: stock moved from one location to another, each a pair of
  adjustments, out and in.
* **Purchases** of materials, from a supplier among the community's contacts,
  with lines that are received into lots.
* **Stores**: a community's connected store on a sales channel, the store's
  locations mapped to Hoard's, its **listings** linked to items, Hoard's
  **copy of each store product**, the **stock pushes** sent to it, the
  **nudges** its webhooks leave for Hoard to refetch a record, and the
  **store fields** mapped to what they fill in Hoard, with the last value the
  store holds of each field Hoard works out.
* **Orders**, by hand for a customer among the community's contacts or copied
  in from a store, with lines; **shipments** of their lines, which record the
  lots they draw; and **returns** of what was shipped.
* **Holds**: a lot kept from use, for a reason among the **hold reasons**,
  until it is released or discarded.
* **Document links**: an Initiative file linked to a lot, a product, a
  material or a base.
* **Task links**: the Initiative task that tracks a run or a purchase.
* **The event outbox**: events recorded with the change that caused them,
  waiting to be sent to Initiative.
* **Stocktakes**: counts of materials or products at a location, whose
  variances become adjustments.
* **Settings**: the organization's one row of them, such as its currency.
* **Images**: pictures of a product, and the one picture of a material or a
  base, which Hoard keeps itself and never offers a store.
* **Bins**: where an item sits at a location, by zone, section and position.
* **Properties** people add to a kind of record, in the UI kit's shapes, and
  each record's **values** of them.
* **Layouts**: how a kind of record is listed and shown on its own page, once
  someone changes it from how Hoard ships it, in the UI kit's shapes. A
  material's, a base's or a product's page is laid out per type.

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


class TypeShape(StrEnum):
    """What a type's page is drawn as until its layout is changed: a shape
    Hoard ships for the kind of record it shapes (:data:`SHAPES`)."""

    #: A material that goes into what is made: its ingredient name, allergens,
    #: fragrance allergens, the weight of one unit, and its declarations.
    INGREDIENT = "ingredient"
    #: A material that holds or labels what is made: those hidden.
    PACKAGING = "packaging"
    BASE = "base"
    #: A product made by its recipe, with the declaration it rolls up.
    MADE = "made"
    #: A product bought finished: its recipe hidden, and its own ingredient
    #: name, allergens and fragrance allergens hidden until they are shown.
    BOUGHT = "bought"
    #: A product sold as what its recipe holds: its stock and lots hidden.
    BUNDLE = "bundle"


#: The shapes each kind's types take, the first its default type's.
SHAPES: dict[CategoryKind, tuple[TypeShape, ...]] = {
    CategoryKind.MATERIAL: (TypeShape.INGREDIENT, TypeShape.PACKAGING),
    CategoryKind.BASE: (TypeShape.BASE,),
    CategoryKind.PRODUCT: (TypeShape.MADE, TypeShape.BOUGHT, TypeShape.BUNDLE),
}

#: The check every type keeps: a shape of its kind's.
SHAPES_CHECK = "(kind::text, shape) IN ({})".format(
    ", ".join(f"('{kind}', '{shape}')" for kind, shapes in SHAPES.items() for shape in shapes)
)


class ItemKind(StrEnum):
    """What an item is."""

    MATERIAL = "material"
    VARIATION = "variation"
    BASE = "base"


class ItemStatus(StrEnum):
    """Whether a product, a material or a base is in use. An archived one
    leaves lists, pickers and search unless they ask for it; its stock,
    history and the recipes naming it stay as they were."""

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
    #: A shipped shipment's line, out.
    SHIPMENT = "shipment"
    #: What came back of a shipment line, in.
    RETURN = "return"
    #: One half of a transfer: out of one location, or into the other.
    TRANSFER = "transfer"


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


class Picking(StrEnum):
    """Which lot of a category's items goes first: the lot pickers are steered
    to, and the lot a draw nobody recorded is assumed to take."""

    EARLIEST_EXPIRY = "earliest_expiry"
    #: By the date the lot was received or made, the oldest first.
    FIRST_IN = "first_in"
    #: The newest first.
    LAST_IN = "last_in"
    #: No lot is assumed: every draw names its lot.
    RECORDED = "recorded"


class HoldEnd(StrEnum):
    """How a hold ended."""

    RELEASED = "released"
    DISCARDED = "discarded"


class Regime(StrEnum):
    """The rules an organization keeps records for, which an admin ticks in
    its settings. Each sets how long its records are kept (``hoard.backups``)."""

    #: US food: the source and recipient of each food (one up, one back).
    US_ONE_UP_ONE_BACK = "us_one_up_one_back"
    #: US food on the Food Traceability List.
    US_FOOD_TRACEABILITY = "us_food_traceability"
    #: US organic certification.
    US_ORGANIC = "us_organic"
    #: US cosmetics: adverse events.
    US_COSMETICS = "us_cosmetics"
    #: EU and UK food traceability.
    EU_FOOD = "eu_food"
    #: EU and UK cosmetics: the product information file and distributors.
    EU_COSMETICS = "eu_cosmetics"
    #: Canada's food traceability.
    CANADA_FOOD = "canada_food"


#: The check every list of regimes keeps: each of :class:`Regime`'s keys.
REGIMES_CHECK = "regimes <@ ARRAY[{}]::text[]".format(", ".join(f"'{regime}'" for regime in Regime))


class Channel(StrEnum):
    """A sales channel a store is on: each has its connector (``hoard.channels``)."""

    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"


class AutoManufacture(StrEnum):
    """Whether a store order makes what it sells: never, always, or only what
    is out of stock. A product or variation with none takes its product's,
    then the organization's."""

    OFF = "off"
    ALWAYS = "always"
    OUT_OF_STOCK = "out_of_stock"


class OrderStatus(StrEnum):
    """Where an order stands. Only an open order commits stock."""

    #: Copied in from a store and not paid yet: it commits nothing.
    PENDING = "pending"
    #: Its lines not yet shipped are committed.
    OPEN = "open"
    #: Every line has shipped, or the store had it fulfilled.
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class ShipmentStatus(StrEnum):
    """Where a shipment stands. Only a shipped one has moved stock."""

    PENDING = "pending"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


class PushStatus(StrEnum):
    """Where a stock push stands."""

    QUEUED = "queued"
    PUSHED = "pushed"
    #: Given up on: its error says why.
    FAILED = "failed"


class MatchedBy(StrEnum):
    """How a listing was linked to its item: by SKU, title or the store's id,
    in that order, or by hand."""

    SKU = "sku"
    TITLE = "title"
    EXTERNAL_ID = "external_id"
    HAND = "hand"


class FieldOwner(StrEnum):
    """What a store's field is kept on: its products, or their variants."""

    PRODUCT = "product"
    VARIANT = "variant"


class FieldTarget(StrEnum):
    """What a store's field fills in Hoard: the allergens or the ingredient
    list Hoard works out from recipes, or a product property."""

    ALLERGENS = "allergens"
    INGREDIENTS = "ingredients"
    PROPERTY = "property"


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


#: The check every list of fragrance allergens keeps: an object.
FRAGRANCE_ALLERGENS_CHECK = "jsonb_typeof(fragrance_allergens) = 'object'"
#: The check every allergen list keeps: each of :class:`Allergen`'s keys.
ALLERGENS_CHECK = "allergens <@ ARRAY[{}]::text[]".format(
    ", ".join(f"'{allergen}'" for allergen in Allergen)
)


def auto_manufacture() -> Any:
    """Whether a store order makes what it sells, or None to take what the
    level above says."""
    return Field(
        default=None, sa_column=enum_column(AutoManufacture, "auto_manufacture", nullable=True)
    )


def item_status() -> Any:
    """Whether the record is in use or archived."""
    return Field(
        default=ItemStatus.ACTIVE,
        sa_column=enum_column(ItemStatus, "item_status", server_default="active"),
    )


def allergens() -> Any:
    """Allergen keys, or None where they are not known."""
    return Field(default=None, sa_column=Column(ARRAY(Text), nullable=True))


def fragrance_allergens() -> Any:
    """Fragrance allergens by key, each with its share in percent, or None
    where they are not known."""
    return Field(default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True))


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
    """A measurement unit: one of Hoard's standard units (``hoard.units``),
    which keeps its standard name and is never renamed or removed, or a unit
    of the community's own."""

    __tablename__ = "units"
    __table_args__ = (
        referable("units"),
        UniqueConstraint("organization_id", "name", name="uq_units_name"),
        Index(
            "uq_units_standard",
            "organization_id",
            "standard",
            unique=True,
            postgresql_where=text("standard IS NOT NULL"),
        ),
    )

    id: int = identity()
    name: str
    #: Which standard unit it is, by key; None for a unit of the community's own.
    standard: str | None = None


class Category(OrganizationRow, Stamped, table=True):
    """A category, typed by the kind of record it sorts."""

    __tablename__ = "categories"
    __table_args__ = (
        referable("categories"),
        UniqueConstraint("organization_id", "kind", "name", name="uq_categories_kind_name"),
        reference("categories", "default_type_id", "item_types"),
    )

    id: int = identity()
    kind: CategoryKind = Field(sa_column=enum_column(CategoryKind, "category_kind"))
    name: str
    #: Which of its items' lots goes first.
    picking: Picking = Field(
        default=Picking.EARLIEST_EXPIRY,
        sa_column=enum_column(Picking, "picking", server_default="earliest_expiry"),
    )
    #: The type a record of its kind takes when it is made in it, unless
    #: another is chosen; cleared when the type is removed.
    default_type_id: int | None = Field(default=None, sa_type=BigInteger)


class ItemType(OrganizationRow, Stamped, table=True):
    """A type of material, base or product: the page its records are shown
    on, never the rules they follow. Each kind has one default type, which
    has no name until someone gives it one (pages call it by the translated
    word for its kind's default) and stays, and a type records name is kept
    while they do."""

    __tablename__ = "item_types"
    __table_args__ = (
        referable("item_types"),
        UniqueConstraint("organization_id", "kind", "name", name="uq_item_types_kind_name"),
        Index(
            "uq_item_types_default",
            "organization_id",
            "kind",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        CheckConstraint("name IS NOT NULL OR is_default", name="ck_item_types_name"),
        CheckConstraint(SHAPES_CHECK, name="ck_item_types_shape"),
    )

    id: int = identity()
    kind: CategoryKind = Field(sa_column=enum_column(CategoryKind, "category_kind"))
    name: str | None = None
    #: What its page is drawn as until its layout is changed: one of its
    #: kind's :class:`TypeShape`.
    shape: str
    is_default: bool = False


class Location(OrganizationRow, Stamped, table=True):
    """A place stock sits. ``main`` marks the one every change defaults to,
    which has no name until someone gives it one: pages call it by the
    translated word for the main location. A **consignee's** location is a
    shop holding the organization's stock to sell, named by its contact:
    its stock is consigned (``hoard.summary``), and it is never mapped to a
    store's location."""

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
        CheckConstraint(
            "NOT consignee OR (contact_id IS NOT NULL AND NOT main)",
            name="ck_locations_consignee",
        ),
    )

    id: int = identity()
    name: str | None = None
    main: bool = False
    #: The Initiative contact holding its address and phone, which a trace
    #: names it by.
    contact_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Whether it is a consignee's: its contact's, never the main location.
    consignee: bool = False


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
        reference("materials", "type_id", "item_types"),
        CheckConstraint("purchase_conversion > 0", name="ck_materials_purchase_conversion"),
        CheckConstraint("unit_weight > 0", name="ck_materials_unit_weight"),
        CheckConstraint(ALLERGENS_CHECK, name="ck_materials_allergens"),
        CheckConstraint(FRAGRANCE_ALLERGENS_CHECK, name="ck_materials_fragrance_allergens"),
    )

    #: Its item's id.
    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str
    status: ItemStatus = item_status()
    sku: str | None = None
    inventoriable: bool = True
    #: The unit stock is counted in.
    tracking_unit_id: int = Field(sa_type=BigInteger)
    #: The unit it is bought in, when not the tracking unit.
    purchase_unit_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Tracking units in one purchase unit.
    purchase_conversion: Decimal = amount(Decimal(1))
    #: What one tracking unit weighs, in grams, for a material not tracked by
    #: weight; only declarations read it.
    unit_weight: Decimal | None = amount(None)
    low_stock_limit: Decimal | None = amount(None)
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Its type, which decides only the page it is shown on; its kind's
    #: default type unless another is given (``hoard.stock``).
    type_id: int = Field(default=None, sa_type=BigInteger)
    #: The preferred supplier: an Initiative contact's id.
    supplier_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The supplier's code for it.
    supplier_code: str | None = None
    #: What an ingredient list calls it, when not its name.
    ingredient_name: str | None = None
    #: The allergens it contains, as its supplier declares them; None until known.
    allergens: list[str] | None = allergens()
    #: The EU fragrance allergens it contains, as its supplier declares them:
    #: each one's share of it in percent, as a decimal string; None until known.
    fragrance_allergens: dict[str, str] | None = fragrance_allergens()
    notes: str | None = None


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
        reference("bases", "type_id", "item_types"),
        CheckConstraint("cure_days > 0", name="ck_bases_cure_days"),
    )

    #: Its item's id.
    id: int = Field(default=None, sa_column=Column(BigInteger, primary_key=True))
    name: str
    status: ItemStatus = item_status()
    sku: str | None = None
    #: The unit stock is counted in.
    tracking_unit_id: int = Field(sa_type=BigInteger)
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Its type, which decides only the page it is shown on; its kind's
    #: default type unless another is given (``hoard.stock``).
    type_id: int = Field(default=None, sa_type=BigInteger)
    low_stock_limit: Decimal | None = amount(None)
    #: How many days what a run makes of it cures before it is used; None for none.
    cure_days: int | None = None
    notes: str | None = None


class Product(OrganizationRow, Stamped, table=True):
    """Sold, through its variations."""

    __tablename__ = "products"
    __table_args__ = (
        referable("products"),
        reference("products", "category_id", "categories"),
        reference("products", "type_id", "item_types"),
        CheckConstraint("cure_days > 0", name="ck_products_cure_days"),
        CheckConstraint(ALLERGENS_CHECK, name="ck_products_allergens"),
        CheckConstraint(FRAGRANCE_ALLERGENS_CHECK, name="ck_products_fragrance_allergens"),
    )

    id: int = identity()
    name: str
    status: ItemStatus = item_status()
    category_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Its type, which decides only the page it is shown on; its kind's
    #: default type unless another is given (``hoard.stock``).
    type_id: int = Field(default=None, sa_type=BigInteger)
    default_price: Decimal | None = amount(None)
    low_stock_limit: Decimal | None = amount(None)
    #: Sold as the variations its recipe holds.
    bundle: bool = False
    #: Whether a store order makes it; None takes the organization's.
    auto_manufacture: AutoManufacture | None = auto_manufacture()
    #: Whether its variations' stock together was at or below its low-stock
    #: limit when ``hoard.events`` last looked.
    low_stock: bool = False
    #: How many days what a run makes of it cures before it is sold; None for none.
    cure_days: int | None = None
    #: Whether it is on the US Food Traceability List.
    traceability_list: bool = False
    #: Whether it is washed off after use, which sets the share past which a
    #: fragrance allergen in it is declared.
    rinse_off: bool = False
    #: What an ingredient list calls it, when not its name, and the allergens
    #: and EU fragrance allergens it contains, as a material records them:
    #: what a variation without a recipe declares (``hoard.declarations``).
    ingredient_name: str | None = None
    allergens: list[str] | None = allergens()
    fragrance_allergens: dict[str, str] | None = fragrance_allergens()


class Variation(OrganizationRow, Stamped, table=True):
    """One form of a product, with its own stock and price. A simple product's
    one variation is its default, with no attributes."""

    __tablename__ = "variations"
    __table_args__ = (
        referable("variations"),
        reference("variations", "id", "items", ondelete="CASCADE"),
        reference("variations", "product_id", "products", ondelete="CASCADE"),
        reference("variations", "purchase_unit_id", "units"),
        CheckConstraint("purchase_conversion > 0", name="ck_variations_purchase_conversion"),
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
    #: The preferred supplier, when it is bought: an Initiative contact's id.
    supplier_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The supplier's code for it.
    supplier_code: str | None = None
    #: The unit it is bought in, when not one at a time.
    purchase_unit_id: int | None = Field(default=None, sa_type=BigInteger)
    #: How many of it one purchase unit holds.
    purchase_conversion: Decimal = amount(Decimal(1))
    #: Whether a store order makes it; None takes its product's.
    auto_manufacture: AutoManufacture | None = auto_manufacture()


class DefaultReason(StrEnum):
    """The adjustment reasons an organization starts with, which people rename
    or remove."""

    CORRECTION = "correction"
    DAMAGED = "damaged"
    EXPIRED = "expired"
    LOST = "lost"
    PERSONAL_USE = "personal_use"
    #: What came back of a shipment damaged, written off rather than restocked.
    RETURNED_DAMAGED = "returned_damaged"
    #: Stock set to a store's count when the store was first synced.
    SYNCED_FROM_STORE = "synced_from_store"


class DefaultHoldReason(StrEnum):
    """The hold reasons an organization starts with, which people rename or
    remove."""

    CURING = "curing"
    AWAITING_RESULTS = "awaiting_results"
    SUSPECT = "suspect"
    RECALL = "recall"


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
    """Materials and products bought from a supplier, received at a location. Its discount
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
    #: The day it is due.
    expected_on: date | None = None
    notes: str | None = None


class PurchaseLine(OrganizationRow, Stamped, table=True):
    """A material or a product's variation bought: so many purchase units at a
    unit cost each, which are so many tracking units (of a variation, so many
    of it). Receiving it makes its lot and its purchase
    adjustment, dated the day it arrived. Receiving part of it splits that
    part off as a line of its own."""

    __tablename__ = "purchase_lines"
    __table_args__ = (
        referable("purchase_lines"),
        reference("purchase_lines", "purchase_id", "purchases", ondelete="CASCADE"),
        reference("purchase_lines", "item_id", "items"),
        reference("purchase_lines", "split_from_id", "purchase_lines"),
        Index("ix_purchase_lines_purchase", "organization_id", "purchase_id"),
        CheckConstraint(
            "purchase_quantity > 0 AND tracking_quantity > 0 AND unit_cost >= 0",
            name="ck_purchase_lines_amounts",
        ),
    )

    id: int = identity()
    purchase_id: int = Field(sa_type=BigInteger)
    #: A material, or a variation of a product that is not a bundle.
    item_id: int = Field(sa_type=BigInteger)
    purchase_quantity: Decimal = amount()
    tracking_quantity: Decimal = amount()
    #: The cost of one purchase unit, before discount and shipping.
    unit_cost: Decimal = amount()
    #: The day it arrived; None while it is on order.
    received_on: date | None = None
    #: The line it was received from in part, which kept the rest on order.
    split_from_id: int | None = Field(default=None, sa_type=BigInteger)


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
        CheckConstraint(FRAGRANCE_ALLERGENS_CHECK, name="ck_lots_fragrance_allergens"),
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
    #: The fragrance allergens its supplier declares for this lot; None where
    #: they are not known, and its item's hold.
    fragrance_allergens: dict[str, str] | None = fragrance_allergens()


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


class Bin(OrganizationRow, Stamped, table=True):
    """Where an item sits at a location: its zone, section and position, any
    of which may be left out. It stays while the item's stock there is
    nothing, and goes with its location. Items may share a bin."""

    __tablename__ = "bins"
    __table_args__ = (
        reference("bins", "item_id", "items", ondelete="CASCADE"),
        reference("bins", "location_id", "locations", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "item_id", "location_id", name="uq_bins_item_location"),
        CheckConstraint("num_nonnulls(zone, section, position) > 0", name="ck_bins_named"),
    )

    id: int = identity()
    item_id: int = Field(sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    zone: str | None = None
    section: str | None = None
    position: str | None = None


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


class Transfer(OrganizationRow, Stamped, table=True):
    """Stock moved from one location to another, as one record: an
    adjustment out of the one and an adjustment into the other, of the same
    item, the same quantity and the same day, each naming its location's lot
    of it or, for stock in no lot, none."""

    __tablename__ = "transfers"
    __table_args__ = (referable("transfers"),)

    id: int = identity()


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
        reference("adjustments", "shipment_line_id", "shipment_lines", ondelete="CASCADE"),
        reference("adjustments", "return_line_id", "return_lines", ondelete="CASCADE"),
        reference("adjustments", "hold_id", "holds"),
        reference("adjustments", "transfer_id", "transfers", ondelete="CASCADE"),
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
        UniqueConstraint("shipment_line_id", name="uq_adjustments_shipment_line_id"),
        UniqueConstraint("return_line_id", "kind", name="uq_adjustments_return_line_kind"),
        UniqueConstraint("transfer_id", "location_id", name="uq_adjustments_transfer_location"),
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
        CheckConstraint(
            "(kind = 'shipment') = (shipment_line_id IS NOT NULL)",
            name="ck_adjustments_shipment",
        ),
        CheckConstraint(
            "(kind <> 'return' OR return_line_id IS NOT NULL) "
            "AND (return_line_id IS NULL OR kind IN ('return', 'manual'))",
            name="ck_adjustments_return",
        ),
        CheckConstraint("hold_id IS NULL OR kind = 'manual'", name="ck_adjustments_hold"),
        CheckConstraint(
            "(kind = 'transfer') = (transfer_id IS NOT NULL)", name="ck_adjustments_transfer"
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
    #: row, the completed production run (one of its lines consumed, or, with
    #: no line, its output made), the shipped shipment line, or the return
    #: line: what came back, in, and, for what came back damaged, the manual
    #: adjustment writing it off; or the transfer it is half of.
    purchase_line_id: int | None = Field(default=None, sa_type=BigInteger)
    stocktake_row_id: int | None = Field(default=None, sa_type=BigInteger)
    production_run_id: int | None = Field(default=None, sa_type=BigInteger)
    production_run_line_id: int | None = Field(default=None, sa_type=BigInteger)
    shipment_line_id: int | None = Field(default=None, sa_type=BigInteger)
    return_line_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The hold a manual adjustment discards its lot from.
    hold_id: int | None = Field(default=None, sa_type=BigInteger)
    transfer_id: int | None = Field(default=None, sa_type=BigInteger)


class OrganizationSettings(OrganizationRow, Stamped, table=True):
    """The organization's settings, one row of them. Until it is made, each
    setting is its default."""

    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_settings_organization_id"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_settings_currency"),
        CheckConstraint(REGIMES_CHECK, name="ck_settings_regimes"),
        CheckConstraint("labour_rate >= 0", name="ck_settings_labour_rate"),
    )

    id: int = identity()
    #: The ISO 4217 code every cost and price is in.
    currency: str = DEFAULT_CURRENCY
    #: What the organization calls a base, such as "blend"; pages say the
    #: translated word for a base until someone names it.
    base_word: str | None = None
    #: Whether an admin, asked which kind of business this is on first opening
    #: Hoard, answered that it is none Hoard has a starting setup for.
    started_blank: bool = False
    #: Whether a store order makes what it sells, where its product says nothing.
    auto_manufacture: AutoManufacture = Field(
        default=AutoManufacture.OFF,
        sa_column=enum_column(AutoManufacture, "auto_manufacture", server_default="off"),
    )
    #: The rules it keeps records for, which an admin ticks: keys of :class:`Regime`.
    regimes: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    )
    #: What an hour of work costs, which prices a recipe's minutes; None until set.
    labour_rate: Decimal | None = amount(None)


class Image(OrganizationRow, Stamped, table=True):
    """A picture of a product, one of its several, or the one picture of a
    material or a base: one of :data:`IMAGE_TYPES`, of at most
    :data:`MAX_IMAGE_BYTES`."""

    __tablename__ = "images"
    __table_args__ = (
        reference("images", "product_id", "products", ondelete="CASCADE"),
        reference("images", "item_id", "items", ondelete="CASCADE"),
        CheckConstraint(
            "content_type IN ('image/gif', 'image/jpeg', 'image/png', 'image/webp')",
            name="ck_images_content_type",
        ),
        CheckConstraint("octet_length(content) BETWEEN 1 AND 5242880", name="ck_images_size"),
        CheckConstraint("num_nonnulls(product_id, item_id) = 1", name="ck_images_of"),
        Index("ix_images_product", "organization_id", "product_id", "position"),
        Index(
            "uq_images_item",
            "organization_id",
            "item_id",
            unique=True,
            postgresql_where=text("item_id IS NOT NULL"),
        ),
    )

    id: int = identity()
    product_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The material or base it pictures.
    item_id: int | None = Field(default=None, sa_type=BigInteger)
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


#: The kinds of record whose page is laid out per type.
TYPED_KINDS = frozenset({RecordKind.MATERIAL, RecordKind.BASE, RecordKind.PRODUCT})


class Layout(OrganizationRow, Stamped, table=True):
    """How a kind of record is listed or shown on its own page, in the UI
    kit's layout shapes. Until it is changed it has no row, and pages draw it
    as Hoard ships it: a type's page as its shape. A material's, a base's or
    a product's page is one per type, and goes with its type."""

    __tablename__ = "layouts"
    __table_args__ = (
        reference("layouts", "type_id", "item_types", ondelete="CASCADE"),
        Index(
            "uq_layouts_view",
            "organization_id",
            "kind",
            "view",
            "type_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(view = 'detail' AND kind IN ({})) = (type_id IS NOT NULL)".format(
                ", ".join(f"'{kind}'" for kind in sorted(TYPED_KINDS))
            ),
            name="ck_layouts_typed",
        ),
    )

    id: int = identity()
    kind: RecordKind = Field(sa_column=enum_column(RecordKind, "record_kind"))
    view: LayoutView = Field(sa_column=enum_column(LayoutView, "layout_view"))
    #: The type whose page it lays out; None for a table, and for a page not
    #: laid out per type.
    type_id: int | None = Field(default=None, sa_type=BigInteger)
    definition: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))


class Recipe(OrganizationRow, Stamped, table=True):
    """What one batch of a variation or a base is made from, and how much of
    it a batch makes; or what a bundle product holds. Each has at most one.

    A recipe is written by amount, each line's amount per batch, or by ratio,
    each line's parts, which its ingredients total splits among the lines
    measured like the total (``hoard.units.recipe``). Either way each line
    keeps its quantity in its item's tracking unit, derived from what was
    written."""

    __tablename__ = "recipes"
    __table_args__ = (
        referable("recipes"),
        reference("recipes", "variation_id", "variations", ondelete="CASCADE"),
        reference("recipes", "base_id", "bases", ondelete="CASCADE"),
        reference("recipes", "product_id", "products", ondelete="CASCADE"),
        reference("recipes", "total_unit_id", "units"),
        UniqueConstraint("organization_id", "variation_id", name="uq_recipes_variation"),
        UniqueConstraint("organization_id", "base_id", name="uq_recipes_base"),
        UniqueConstraint("organization_id", "product_id", name="uq_recipes_product"),
        CheckConstraint(
            "num_nonnulls(variation_id, base_id, product_id) = 1", name="ck_recipes_output"
        ),
        CheckConstraint(
            "batch_quantity > 0 AND (minutes IS NULL OR minutes >= 0)", name="ck_recipes_batch"
        ),
        CheckConstraint(
            "by_ratio = (total IS NOT NULL) AND by_ratio = (total_unit_id IS NOT NULL) "
            "AND total > 0",
            name="ck_recipes_ratio",
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
    #: Whether its lines are written as parts of a ratio.
    by_ratio: bool = False
    #: A ratio's ingredients total, which its parts split, and its unit.
    total: Decimal | None = amount(None)
    total_unit_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Its method, in words: shown above its lines.
    notes: str | None = None


class RecipeLine(OrganizationRow, Stamped, table=True):
    """One item a batch takes, and how much of it, in its tracking unit: a
    material or a base, or, in a bundle's recipe, a variation. Its quantity is
    derived from what was written: an amount, or parts of a ratio, in a unit
    that converts to the tracking unit."""

    __tablename__ = "recipe_lines"
    __table_args__ = (
        reference("recipe_lines", "recipe_id", "recipes", ondelete="CASCADE"),
        reference("recipe_lines", "item_id", "items"),
        reference("recipe_lines", "unit_id", "units"),
        UniqueConstraint("organization_id", "recipe_id", "item_id", name="uq_recipe_lines_item"),
        CheckConstraint("quantity > 0 AND written > 0", name="ck_recipe_lines_quantity"),
    )

    id: int = identity()
    recipe_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    #: In the item's tracking unit, for one batch.
    quantity: Decimal = amount()
    #: The number as typed: the amount, or the parts of a ratio.
    written: Decimal = amount()
    #: The unit it was written in; None for a variation, which counts in units
    #: of itself.
    unit_id: int | None = Field(default=None, sa_type=BigInteger)
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


class HoldReason(OrganizationRow, Stamped, table=True):
    """Why a lot is on hold. A default reason carries its ``key`` and has no
    name until someone gives it one, as an adjustment reason does; a setup's
    reason carries its key and the name it was added with."""

    __tablename__ = "hold_reasons"
    __table_args__ = (
        referable("hold_reasons"),
        UniqueConstraint("organization_id", "name", name="uq_hold_reasons_name"),
        UniqueConstraint("organization_id", "key", name="uq_hold_reasons_key"),
        CheckConstraint("name IS NOT NULL OR key IS NOT NULL", name="ck_hold_reasons_name"),
    )

    id: int = identity()
    key: str | None = None
    name: str | None = None


class Hold(OrganizationRow, Stamped, table=True):
    """A lot kept from being used, shipped or offered to a store, for a
    reason, until it is released or discarded. A lot has at most one open
    hold; ended ones are kept."""

    __tablename__ = "holds"
    __table_args__ = (
        referable("holds"),
        reference("holds", "lot_id", "lots", ondelete="CASCADE"),
        reference("holds", "reason_id", "hold_reasons"),
        reference("holds", "run_id", "production_runs"),
        reference("holds", "order_id", "orders"),
        reference("holds", "from_hold_id", "holds"),
        Index("ix_holds_lot", "organization_id", "lot_id"),
        Index(
            "uq_holds_open",
            "organization_id",
            "lot_id",
            unique=True,
            postgresql_where=text("ended IS NULL"),
        ),
        CheckConstraint("(ended IS NULL) = (ended_on IS NULL)", name="ck_holds_ended"),
    )

    id: int = identity()
    lot_id: int = Field(sa_type=BigInteger)
    reason_id: int = Field(sa_type=BigInteger)
    #: The day before which it is not released, such as the end of a cure.
    ready_on: date | None = None
    notes: str | None = None
    #: The production run that put its lot on hold, curing.
    run_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The order it was placed from, when a problem with the order was reported.
    order_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The hold whose lot this one's was made from, when it was placed with
    #: everything made from that lot.
    from_hold_id: int | None = Field(default=None, sa_type=BigInteger)
    ended_on: date | None = None
    #: How it ended; None while it is open.
    ended: HoldEnd | None = Field(
        default=None, sa_column=enum_column(HoldEnd, "hold_end", nullable=True)
    )
    #: When its ``hold-ready`` event was recorded, so it is recorded once.
    ready_sent_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class DocumentLink(OrganizationRow, Stamped, table=True):
    """An Initiative file linked to one lot, product, material or base, such
    as a supplier's certificate or a report of a problem. Hoard keeps only the
    file's id."""

    __tablename__ = "document_links"
    __table_args__ = (
        reference("document_links", "lot_id", "lots", ondelete="CASCADE"),
        reference("document_links", "product_id", "products", ondelete="CASCADE"),
        reference("document_links", "material_id", "materials", ondelete="CASCADE"),
        reference("document_links", "base_id", "bases", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "lot_id", "file_id", name="uq_document_links_lot"),
        UniqueConstraint(
            "organization_id", "product_id", "file_id", name="uq_document_links_product"
        ),
        UniqueConstraint(
            "organization_id", "material_id", "file_id", name="uq_document_links_material"
        ),
        UniqueConstraint("organization_id", "base_id", "file_id", name="uq_document_links_base"),
        CheckConstraint(
            "num_nonnulls(lot_id, product_id, material_id, base_id) = 1",
            name="ck_document_links_record",
        ),
    )

    id: int = identity()
    #: The file's Initiative id.
    file_id: int = Field(sa_type=BigInteger)
    lot_id: int | None = Field(default=None, sa_type=BigInteger)
    product_id: int | None = Field(default=None, sa_type=BigInteger)
    material_id: int | None = Field(default=None, sa_type=BigInteger)
    base_id: int | None = Field(default=None, sa_type=BigInteger)


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


class Store(OrganizationRow, Stamped, table=True):
    """The organization's store on one sales channel, made when its connection
    is first saved (``hoard.channels.hooks``): at most one per channel.

    ``last_check_error`` keeps why saving the connection was last refused,
    until it is saved again. The ``*_since`` stamps say how far the store's
    orders and products have been read. ``imported_at`` says the store is
    synced (``hoard.store_sync``): it is when its sync began reading the
    store's orders, and a fulfillment the store sent before then is history.
    A connected store without it is pending."""

    __tablename__ = "stores"
    __table_args__ = (
        referable("stores"),
        UniqueConstraint("organization_id", "channel", name="uq_stores_channel"),
    )

    id: int = identity()
    channel: Channel = Field(sa_column=enum_column(Channel, "channel"))
    #: The store's own address, such as ``shop.myshopify.com``, as the store
    #: answered it; None until a connection is saved.
    shop_domain: str | None = None
    #: What the store calls itself.
    account_label: str | None = None
    #: Whether its connection is saved now: false once it is cleared.
    connected: bool = False
    #: Why saving the connection was last refused: a code pages translate.
    last_check_error: str | None = None
    orders_since: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    products_since: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    imported_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StoreLocation(OrganizationRow, Stamped, table=True):
    """One of a store's locations, by the store's id, and the Hoard location
    whose stock it sells; none until someone maps it."""

    __tablename__ = "store_locations"
    __table_args__ = (
        referable("store_locations"),
        reference("store_locations", "store_id", "stores", ondelete="CASCADE"),
        reference("store_locations", "location_id", "locations"),
        UniqueConstraint(
            "organization_id", "store_id", "external_id", name="uq_store_locations_external"
        ),
    )

    id: int = identity()
    store_id: int = Field(sa_type=BigInteger)
    #: The store's id for it.
    external_id: str
    #: The store's name for it.
    name: str | None = None
    location_id: int | None = Field(default=None, sa_type=BigInteger)


class Listing(OrganizationRow, Stamped, table=True):
    """A store's product or variant, by the store's ids, and the item it sells:
    none until it is matched. Several listings may sell one item."""

    __tablename__ = "listings"
    __table_args__ = (
        referable("listings"),
        reference("listings", "store_id", "stores", ondelete="CASCADE"),
        reference("listings", "item_id", "items"),
        UniqueConstraint(
            "organization_id",
            "store_id",
            "product_id",
            "variant_id",
            name="uq_listings_external",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_listings_item", "organization_id", "item_id"),
        CheckConstraint("(item_id IS NULL) = (matched_by IS NULL)", name="ck_listings_matched"),
    )

    id: int = identity()
    store_id: int = Field(sa_type=BigInteger)
    item_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The store's ids: its product, its variant where it has variants, and
    #: the inventory item its stock is kept under, where the store has one.
    product_id: str
    variant_id: str | None = None
    inventory_item_id: str | None = None
    sku: str | None = None
    title: str | None = None
    #: The variant's price in the store.
    price: Decimal | None = amount(None)
    matched_by: MatchedBy | None = Field(
        default=None, sa_column=enum_column(MatchedBy, "matched_by", nullable=True)
    )


class StoreProductCopy(OrganizationRow, Stamped, table=True):
    """Hoard's copy of a store's product, as the store last answered it: what
    pages show without asking the store. Each variant's SKU and price are on
    its listing; ``variants`` keeps each one's id, title and option values, in
    the store's order. ``images`` keeps each image's id, address and alt text;
    ``published_on`` the store's ids for the sales channels showing it."""

    __tablename__ = "store_products"
    __table_args__ = (
        reference("store_products", "store_id", "stores", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "store_id", "product_id", name="uq_store_products_external"
        ),
    )

    id: int = identity()
    store_id: int = Field(sa_type=BigInteger)
    #: The store's id for it.
    product_id: str
    title: str
    #: As the store keeps it: HTML.
    description: str | None = None
    #: The store's word for whether it sells it, such as ``active`` or ``draft``.
    status: str | None = None
    product_type: str | None = None
    vendor: str | None = None
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(ARRAY(Text), nullable=False, server_default=text("'{}'")),
    )
    #: The names of its options, in order.
    options: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    variants: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    images: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    published_on: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    #: When the store last changed it, as the store said.
    store_updated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StoreFieldMapping(OrganizationRow, Stamped, table=True):
    """One of a store's fields, by its ``namespace.key``, and what it fills in
    Hoard (``hoard.store_fields``); a field filling nothing has no row.

    ``pairs`` pairs each value the store keeps for it with Hoard's: for
    allergens, an allergen's key, or None where none is paired yet; for a
    select property, the option's value."""

    __tablename__ = "store_fields"
    __table_args__ = (
        referable("store_fields"),
        reference("store_fields", "store_id", "stores", ondelete="CASCADE"),
        reference("store_fields", "property_id", "property_definitions", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "store_id", "owner", "handle", name="uq_store_fields_handle"
        ),
        CheckConstraint(
            "(target = 'property') = (property_id IS NOT NULL)", name="ck_store_fields_property"
        ),
    )

    id: int = identity()
    store_id: int = Field(sa_type=BigInteger)
    owner: FieldOwner = Field(sa_column=enum_column(FieldOwner, "field_owner"))
    #: The store's ``namespace.key`` for it.
    handle: str
    #: The store's name for it, and its type's.
    name: str
    type: str
    target: FieldTarget = Field(sa_column=enum_column(FieldTarget, "field_target"))
    property_id: int | None = Field(default=None, sa_type=BigInteger)
    pairs: dict[str, str | None] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )


class StoreFieldValue(OrganizationRow, table=True):
    """A field Hoard works out, on one of the store's products or variants:
    what the store holds of it, as Hoard last sent it or read it, and Hoard's
    own value when it last compared them, each as the store keeps it. None
    for no value, and for Hoard's, one it does not know."""

    __tablename__ = "store_field_values"
    __table_args__ = (
        reference("store_field_values", "field_id", "store_fields", ondelete="CASCADE"),
        UniqueConstraint(
            "organization_id", "field_id", "owner_id", name="uq_store_field_values_owner"
        ),
    )

    id: int = identity()
    field_id: int = Field(sa_type=BigInteger)
    #: The store's id for the product or variant.
    owner_id: str
    store_value: str | None = None
    hoard_value: str | None = None


class StockPush(OrganizationRow, table=True):
    """Setting a listing's stock at one of its store's locations: queued,
    tried until the store takes it, and kept as the push log. ``compare_quantity``
    is what Hoard last knew the store held, which a store that compares
    before it sets checks; ``idempotency_key`` makes a retry the same push."""

    __tablename__ = "stock_pushes"
    __table_args__ = (
        reference("stock_pushes", "listing_id", "listings", ondelete="CASCADE"),
        reference("stock_pushes", "store_location_id", "store_locations", ondelete="CASCADE"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_stock_pushes_key"),
        Index("ix_stock_pushes_due", "organization_id", "status", "next_attempt_at"),
        Index("ix_stock_pushes_listing", "organization_id", "listing_id", "created_at"),
        CheckConstraint("attempts >= 0", name="ck_stock_pushes_attempts"),
    )

    id: int = identity()
    listing_id: int = Field(sa_type=BigInteger)
    store_location_id: int | None = Field(default=None, sa_type=BigInteger)
    #: Whole units: what stores count in.
    quantity: int
    compare_quantity: int | None = None
    idempotency_key: str
    status: PushStatus = Field(
        default=PushStatus.QUEUED,
        sa_column=enum_column(PushStatus, "push_status", server_default="queued"),
    )
    attempts: int = 0
    next_attempt_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    #: Why the store did not take it.
    error: str | None = None
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    pushed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StoreNudge(OrganizationRow, table=True):
    """A store's webhook saying one of its records changed: Hoard refetches the
    record, then marks it done. One waits per record at a time until the
    worker takes it (``taken_at``); a webhook arriving after that leaves a
    new one. An order's keeps the webhook's own body, the newest delivered,
    which Hoard copies when the store no longer returns the order."""

    __tablename__ = "store_nudges"
    __table_args__ = (
        reference("store_nudges", "store_id", "stores", ondelete="CASCADE"),
        Index(
            "uq_store_nudges_waiting",
            "organization_id",
            "store_id",
            "topic",
            "external_id",
            unique=True,
            postgresql_where=text("done_at IS NULL AND taken_at IS NULL"),
        ),
        Index("ix_store_nudges_due", "organization_id", "done_at", "next_attempt_at"),
        CheckConstraint("attempts >= 0", name="ck_store_nudges_attempts"),
    )

    id: int = identity()
    store_id: int = Field(sa_type=BigInteger)
    #: What changed: one of ``hoard.channels.NudgeTopic``.
    topic: str
    #: The store's id for the record.
    external_id: str
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    attempts: int = 0
    next_attempt_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    #: When the worker took it to refetch its record.
    taken_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    done_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    #: The webhook's body, for an order: what the order was when it was sent.
    body: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))


class Order(OrganizationRow, Stamped, table=True):
    """Goods sold, shipped from a location: made by hand for a customer among
    the community's contacts, or copied in from a store, where the store's
    order number stands for a consumer. Its shipping and tax are not goods;
    its discount is taken off its lines' total."""

    __tablename__ = "orders"
    __table_args__ = (
        referable("orders"),
        reference("orders", "store_id", "stores"),
        reference("orders", "location_id", "locations"),
        UniqueConstraint("organization_id", "code", name="uq_orders_code"),
        UniqueConstraint("organization_id", "store_id", "external_id", name="uq_orders_external"),
        Index("ix_orders_status", "organization_id", "status"),
        Index("ix_orders_store_customer", "organization_id", "store_id", "store_customer"),
        CheckConstraint("(store_id IS NULL) = (external_id IS NULL)", name="ck_orders_external"),
        CheckConstraint(
            "store_id IS NOT NULL OR customer_contact_id IS NOT NULL", name="ck_orders_customer"
        ),
        CheckConstraint(
            "shipping >= 0 AND tax >= 0 AND discount >= 0 AND refunded >= 0",
            name="ck_orders_amounts",
        ),
    )

    id: int = identity()
    code: str
    #: The store it was copied from, and the store's id and number for it.
    store_id: int | None = Field(default=None, sa_type=BigInteger)
    external_id: str | None = None
    external_number: str | None = None
    #: The customer: an Initiative contact's id. None for a store's consumer.
    customer_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    #: The initiative it was made from.
    initiative_id: int | None = Field(default=None, sa_type=BigInteger)
    ordered_on: date
    due_on: date | None = None
    paid: bool = False
    status: OrderStatus = Field(
        default=OrderStatus.OPEN,
        sa_column=enum_column(OrderStatus, "order_status", server_default="open"),
    )
    shipping: Decimal = amount()
    tax: Decimal = amount()
    discount: Decimal = amount()
    #: What the store has refunded of it.
    refunded: Decimal = amount()
    delivery_address: str | None = None
    #: The store's number for its customer, for a store's order; Hoard keeps
    #: nothing else about them.
    store_customer: str | None = None
    #: When the store last changed it, as of the copy Hoard holds.
    store_updated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class OrderLine(OrganizationRow, Stamped, table=True):
    """A variation sold, so many at a price each. A bundle's parts are lines
    of their own, each naming the bundle's line (``bundle_line_id``), and the
    bundle's own line commits nothing. A line may name the run that made it.
    A store's line keeps how many it sells now, after the store's edits and
    refunds, beside how many were ordered."""

    __tablename__ = "order_lines"
    __table_args__ = (
        referable("order_lines"),
        reference("order_lines", "order_id", "orders", ondelete="CASCADE"),
        reference("order_lines", "item_id", "items"),
        reference("order_lines", "bundle_line_id", "order_lines", ondelete="CASCADE"),
        reference("order_lines", "production_run_id", "production_runs"),
        Index("ix_order_lines_order", "organization_id", "order_id"),
        Index("ix_order_lines_item", "organization_id", "item_id"),
        CheckConstraint(
            "quantity > 0 AND price >= 0 AND current_quantity >= 0 AND refunded_quantity >= 0",
            name="ck_order_lines_amounts",
        ),
    )

    id: int = identity()
    order_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    #: How many were ordered.
    quantity: Decimal = amount()
    #: How many a store's line sells now, after edits and refunds; None for a
    #: line that sells its whole quantity, as a hand order's does.
    current_quantity: Decimal | None = amount(None)
    #: How many the store refunded.
    refunded_quantity: Decimal = amount()
    #: What one sold for.
    price: Decimal = amount()
    #: The store's id for the line.
    external_line_id: str | None = None
    bundle_line_id: int | None = Field(default=None, sa_type=BigInteger)
    production_run_id: int | None = Field(default=None, sa_type=BigInteger)
    #: The store's selling plan, for a subscription's order.
    selling_plan: str | None = None


class Shipment(OrganizationRow, Stamped, table=True):
    """Some of an order's lines sent from a location, by hand or from the
    store's fulfillment (``external_id``). Only shipping it moves stock: each
    line out, from its lot."""

    __tablename__ = "shipments"
    __table_args__ = (
        referable("shipments"),
        reference("shipments", "order_id", "orders", ondelete="CASCADE"),
        reference("shipments", "location_id", "locations"),
        UniqueConstraint(
            "organization_id", "order_id", "external_id", name="uq_shipments_external"
        ),
        Index("ix_shipments_order", "organization_id", "order_id"),
        CheckConstraint(
            "status <> 'shipped' OR shipped_on IS NOT NULL", name="ck_shipments_shipped"
        ),
    )

    id: int = identity()
    order_id: int = Field(sa_type=BigInteger)
    location_id: int = Field(sa_type=BigInteger)
    #: The store's id for the fulfillment it records.
    external_id: str | None = None
    status: ShipmentStatus = Field(
        default=ShipmentStatus.PENDING,
        sa_column=enum_column(ShipmentStatus, "shipment_status", server_default="pending"),
    )
    shipped_on: date | None = None
    #: The carrier: an Initiative contact's id.
    carrier_contact_id: int | None = Field(default=None, sa_type=BigInteger)
    tracking: str | None = None
    delivery_address: str | None = None
    #: The customer's or carrier's reference for it.
    reference: str | None = None


class ShipmentLine(OrganizationRow, Stamped, table=True):
    """What a shipment sends of an order line, and from which lot: the lot
    someone recorded, or the one Hoard assumed (``assumed``)."""

    __tablename__ = "shipment_lines"
    __table_args__ = (
        referable("shipment_lines"),
        reference("shipment_lines", "shipment_id", "shipments", ondelete="CASCADE"),
        reference("shipment_lines", "order_line_id", "order_lines", ondelete="CASCADE"),
        reference("shipment_lines", "item_id", "items"),
        reference("shipment_lines", "lot_id", "lots"),
        Index("ix_shipment_lines_shipment", "organization_id", "shipment_id"),
        Index("ix_shipment_lines_order_line", "organization_id", "order_line_id"),
        CheckConstraint("quantity > 0", name="ck_shipment_lines_quantity"),
    )

    id: int = identity()
    shipment_id: int = Field(sa_type=BigInteger)
    order_line_id: int = Field(sa_type=BigInteger)
    item_id: int = Field(sa_type=BigInteger)
    lot_id: int | None = Field(default=None, sa_type=BigInteger)
    quantity: Decimal = amount()
    assumed: bool = False


class ReturnLine(OrganizationRow, Stamped, table=True):
    """What came back of a shipment line, and when. What came back damaged is
    written off with the reason for it rather than restocked."""

    __tablename__ = "return_lines"
    __table_args__ = (
        referable("return_lines"),
        reference("return_lines", "shipment_line_id", "shipment_lines", ondelete="CASCADE"),
        Index("ix_return_lines_shipment_line", "organization_id", "shipment_line_id"),
        CheckConstraint("quantity > 0", name="ck_return_lines_quantity"),
    )

    id: int = identity()
    shipment_line_id: int = Field(sa_type=BigInteger)
    quantity: Decimal = amount()
    dated_on: date
    damaged: bool = False
