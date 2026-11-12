"""Hoard's reports: what to reorder, what stock is worth, what was wasted,
what production planned against what it made, and which allergens each product
contains.

* :func:`reorder`: the materials, bases and products whose stock is at or
  below their low-stock limit: to buy, or to make. A product's stock is its
  variations' together. A material that carries no stock, and an archived
  product, are not reordered.
* :func:`valuation`: what the stock on hand is worth at each item's average
  unit cost (``hoard.costing``), per item and in total, in the organization's
  currency.

Both count stock at every location together, and ``hoard.endpoints`` and the
Reports page share them. The reads below answer an endpoint call themselves:

* :func:`waste`: stock taken out by hand with a reason, other than a
  correction, by reason. It reads whatever reasons the community has.
* :func:`planned_against_actual`: what completed production runs planned to
  make against what they made.
* :func:`allergen_matrix`: which allergens each active product contains
  (``hoard.declarations``).

The two reads over time look back ``days`` days, :data:`DAYS` when a caller
does not say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.declarations import Declarations
from hoard.events import item_name
from hoard.records import (
    DEFAULT_CURRENCY,
    PLACES,
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Allergen,
    Base,
    CategoryKind,
    DefaultReason,
    Item,
    ItemKind,
    Material,
    OrganizationSettings,
    Product,
    ProductionRun,
    ProductStatus,
    Recipe,
    RunStatus,
    StockLevel,
    Unit,
    Variation,
)
from seneschal_core.platform import EndpointCall, organization_session


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


#: How many days back a read looks when its caller does not say.
DAYS = 30


def _since(call: EndpointCall) -> datetime:
    days = call.params.get("days")
    back = days if isinstance(days, int) and days > 0 else DAYS
    return datetime.now(UTC) - timedelta(days=back)


async def _unit(db: AsyncSession, item_id: int) -> str | None:
    """The unit a material's or a base's stock is counted in; a variation's is
    counted in each, which has none."""
    for kind in (Material, Base):
        found = await db.get(kind, item_id)
        if found is not None:
            unit = await db.get(Unit, found.tracking_unit_id)
            return unit.name if unit else None
    return None


async def waste(call: EndpointCall) -> dict[str, Any]:
    """Answer ``waste``: what was thrown away over the last ``days``, by reason
    in the order the reasons were made, each reason's items the most first. A
    reason nobody has named is answered by its key."""
    since = _since(call).date()
    wasted = -func.sum(Adjustment.change)
    query = (
        select(
            func.coalesce(AdjustmentReason.name, AdjustmentReason.key),
            Adjustment.item_id,
            wasted,
        )
        .join(AdjustmentReason, col(AdjustmentReason.id) == Adjustment.reason_id)
        .where(
            Adjustment.kind == AdjustmentKind.MANUAL,
            Adjustment.change < 0,
            Adjustment.dated_on >= since,
            or_(
                col(AdjustmentReason.key).is_(None),
                col(AdjustmentReason.key) != DefaultReason.CORRECTION.value,
            ),
        )
        .group_by(col(AdjustmentReason.id), col(Adjustment.item_id))
        .order_by(col(AdjustmentReason.id), wasted.desc())
    )
    columns: dict[str, list[Any]] = {"reason": [], "item": [], "unit": [], "quantity": []}
    async with organization_session(call) as db:
        for reason, item_id, quantity in (await db.exec(query)).all():
            columns["reason"].append(reason)
            columns["item"].append(await item_name(db, item_id))
            columns["unit"].append(await _unit(db, item_id))
            columns["quantity"].append(str(quantity))
    return columns


async def planned_against_actual(call: EndpointCall) -> dict[str, Any]:
    """Answer ``yield``: each run completed over the last ``days``, the latest
    first, with what its batches of its recipe planned to make. A run whose
    recipe is gone planned nothing anyone can say."""
    since = _since(call).date()
    query = (
        select(ProductionRun, col(Recipe.batch_quantity))
        .outerjoin(Recipe, col(Recipe.id) == ProductionRun.recipe_id)
        .where(
            ProductionRun.status == RunStatus.COMPLETED, col(ProductionRun.completed_on) >= since
        )
        .order_by(col(ProductionRun.completed_on).desc(), col(ProductionRun.id).desc())
    )
    columns: dict[str, list[Any]] = {
        key: [] for key in ("code", "item", "completed_on", "unit", "planned", "actual")
    }
    async with organization_session(call) as db:
        for run, batch_quantity in (await db.exec(query)).all():
            planned: Decimal | None = (
                None
                if batch_quantity is None
                else (run.planned_batches * batch_quantity).quantize(PLACES)
            )
            columns["code"].append(run.code)
            columns["item"].append(await item_name(db, run.item_id))
            columns["completed_on"].append(run.completed_on and run.completed_on.isoformat())
            columns["unit"].append(await _unit(db, run.item_id))
            columns["planned"].append(None if planned is None else str(planned))
            columns["actual"].append(str(run.actual_quantity))
    return columns


async def allergen_matrix(call: EndpointCall) -> dict[str, Any]:
    """Answer ``allergen-matrix``: each active product by name, and which
    allergens it contains (``hoard.declarations``)."""
    columns: dict[str, list[Any]] = {"product": [], **{allergen.value: [] for allergen in Allergen}}
    async with organization_session(call) as db:
        declarations = Declarations(db)
        products = await db.exec(
            select(Product)
            .where(Product.status == ProductStatus.ACTIVE)
            .order_by(col(Product.name), col(Product.id))
        )
        for product in products.all():
            contains = set((await declarations.product(product)).allergens)
            columns["product"].append(product.name)
            for allergen in Allergen:
                columns[allergen.value].append(allergen in contains)
    return columns
