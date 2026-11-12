"""Reports on stock: what to reorder, and what stock is worth.

* :func:`reorder`: the materials, bases and products whose stock is at or
  below their low-stock limit: to buy, or to make. A product's stock is its
  variations' together. A material that carries no stock, and an archived
  product, are not reordered.
* :func:`valuation`: what the stock on hand is worth at each item's average
  unit cost (``hoard.costing``), per item and in total, in the organization's
  currency.

Both count stock at every location together.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    DEFAULT_CURRENCY,
    PLACES,
    Base,
    CategoryKind,
    Item,
    ItemKind,
    Material,
    OrganizationSettings,
    Product,
    ProductStatus,
    StockLevel,
    Unit,
    Variation,
)


@dataclass(frozen=True)
class Shortfall:
    """A material, base or product to reorder: what is on hand, against its limit."""

    kind: CategoryKind
    #: The material's, the base's or the product's id.
    id: int
    name: str
    #: The material's or the base's tracking unit; a product counts in units of itself.
    unit: str | None
    on_hand: Decimal
    low_stock_limit: Decimal


@dataclass(frozen=True)
class ItemValue:
    """What one item's stock is worth."""

    item_id: int
    kind: ItemKind
    #: The material's or the base's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a material or a base.
    attributes: dict[str, str]
    on_hand: Decimal
    average_cost: Decimal
    value: Decimal


@dataclass(frozen=True)
class Valuation:
    """What the stock is worth: each item holding any, and their total."""

    currency: str
    items: list[ItemValue]
    total: Decimal


async def currency(db: AsyncSession) -> str:
    """The organization's currency: an ISO 4217 code."""
    settings = (await db.exec(select(OrganizationSettings))).first()
    return settings.currency if settings else DEFAULT_CURRENCY


def _stock():
    """Each item's stock at every location together."""
    return (
        select(StockLevel.item_id, func.sum(StockLevel.quantity).label("on_hand"))
        .group_by(col(StockLevel.item_id))
        .subquery()
    )


async def reorder(db: AsyncSession) -> list[Shortfall]:
    """The materials, bases and products at or below their low-stock limit, by name."""
    stock = _stock()
    material_on_hand = func.coalesce(stock.c.on_hand, 0)
    materials = await db.exec(
        select(Material, Unit.name, material_on_hand)
        .join(Unit, col(Unit.id) == Material.tracking_unit_id)
        .outerjoin(stock, stock.c.item_id == Material.id)
        .where(
            col(Material.inventoriable),
            col(Material.low_stock_limit).is_not(None),
            material_on_hand <= Material.low_stock_limit,
        )
    )
    base_on_hand = func.coalesce(stock.c.on_hand, 0)
    bases = await db.exec(
        select(Base, Unit.name, base_on_hand)
        .join(Unit, col(Unit.id) == Base.tracking_unit_id)
        .outerjoin(stock, stock.c.item_id == Base.id)
        .where(col(Base.low_stock_limit).is_not(None), base_on_hand <= Base.low_stock_limit)
    )
    product_on_hand = func.coalesce(func.sum(stock.c.on_hand), 0)
    products = await db.exec(
        select(Product, product_on_hand)
        .join(Variation, col(Variation.product_id) == Product.id)
        .outerjoin(stock, stock.c.item_id == Variation.id)
        .where(Product.status == ProductStatus.ACTIVE, col(Product.low_stock_limit).is_not(None))
        .group_by(col(Product.id))
        .having(product_on_hand <= Product.low_stock_limit)
    )
    shortfalls = [
        Shortfall(
            CategoryKind.MATERIAL,
            material.id,
            material.name,
            unit,
            Decimal(on_hand),
            Decimal(material.low_stock_limit or 0),
        )
        for material, unit, on_hand in materials
    ]
    shortfalls += [
        Shortfall(
            CategoryKind.BASE,
            base.id,
            base.name,
            unit,
            Decimal(on_hand),
            Decimal(base.low_stock_limit or 0),
        )
        for base, unit, on_hand in bases
    ]
    shortfalls += [
        Shortfall(
            CategoryKind.PRODUCT,
            product.id,
            product.name,
            None,
            Decimal(on_hand),
            Decimal(product.low_stock_limit or 0),
        )
        for product, on_hand in products
    ]
    return sorted(shortfalls, key=lambda shortfall: (shortfall.name.casefold(), shortfall.id))


async def valuation(db: AsyncSession) -> Valuation:
    """What the stock on hand is worth: each item holding any, by name, and
    their total."""
    stock = _stock()
    name = func.coalesce(Material.name, Base.name, Product.name)
    rows = await db.exec(
        select(Item, name, Variation.attributes, stock.c.on_hand)
        .join(stock, stock.c.item_id == Item.id)
        .outerjoin(Material, col(Material.id) == Item.id)
        .outerjoin(Base, col(Base.id) == Item.id)
        .outerjoin(Variation, col(Variation.id) == Item.id)
        .outerjoin(Product, col(Product.id) == Variation.product_id)
        .where(stock.c.on_hand != 0)
        .order_by(func.lower(name), col(Item.id))
    )
    items = [
        ItemValue(
            item_id=item.id,
            kind=item.kind,
            name=item_name,
            attributes=dict(attributes or {}),
            on_hand=Decimal(on_hand),
            average_cost=item.average_cost,
            value=(Decimal(on_hand) * item.average_cost).quantize(PLACES),
        )
        for item, item_name, attributes, on_hand in rows
    ]
    return Valuation(
        currency=await currency(db),
        items=items,
        total=sum((item.value for item in items), Decimal(0)),
    )
