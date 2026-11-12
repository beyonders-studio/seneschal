"""Cost of goods, and the mass balance, over a period from ``start`` to ``end``,
both days included.

* :func:`cost_of_goods`: what the goods cost over the period, in the shape a
  tax return asks for it: the stock's value at the start, plus starting stock
  entered and purchases received during the period, less stock taken for
  personal use, less the stock's value at the end. Stock is valued at each
  item's moving average cost on the day (``hoard.costing``): materials, and the
  material cost of bases and products not yet shipped. Labour and purchase tax
  stay out. Beside it, what the goods shipped over the period cost, less what
  came back, and how many runs completed in it were uncosted.
* :func:`mass_balance`: each item's quantities over the period: what it had at
  the start, what was received, made, used in production, sold and lost, and
  what it had at the end, so that opening + received + made − used − sold −
  lost = closing. Split by a lot property, each lot's stock counts under the
  option its lot holds, and stock moved with no lot, or from a lot holding
  none, under no option. Received against sold for one option is the volume
  in against sold of a certification such as fair trade.

Both count every location together. The mass balance holds quantities alone,
so every member reads it; cost of goods only a member who sees costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby
from typing import Any

from sqlalchemy import Text, func, literal_column
from sqlalchemy import select as sa_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Base,
    DefaultReason,
    Item,
    ItemKind,
    Material,
    Product,
    ProductionRun,
    PropertyValue,
    RunStatus,
    Unit,
    Variation,
)
from hoard.reports import currency


@dataclass(frozen=True)
class CostOfGoods:
    """What the goods cost over a period, in the organization's currency."""

    currency: str
    #: The stock's value at the start of the period's first day.
    opening: Decimal
    #: Starting stock entered during the period, at the cost typed in.
    starting: Decimal
    #: Purchases received during the period, at their landed cost.
    purchases: Decimal
    #: Stock taken for personal use during the period, at its cost on the day.
    personal_use: Decimal
    #: The stock's value at the end of the period's last day.
    closing: Decimal
    #: opening + starting + purchases − personal use − closing.
    cost_of_goods: Decimal
    #: What the goods shipped during the period cost, less what came back.
    shipped: Decimal
    #: Production runs completed during the period that consumed something
    #: with no cost, so that what they made is valued too low.
    uncosted_runs: int


@dataclass(frozen=True)
class Balance:
    """One item's quantities over a period, under one option of the property
    the balance is split by."""

    item_id: int
    kind: ItemKind
    #: The material's or the base's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a material or a base.
    attributes: dict[str, str]
    #: The material's or the base's tracking unit; a variation counts in units of itself.
    unit: str | None
    #: The option its lots hold; none for stock in no lot, or in a lot holding
    #: none, and for every row of a balance not split.
    option: str | None
    opening: Decimal
    received: Decimal
    made: Decimal
    used: Decimal
    sold: Decimal
    lost: Decimal
    closing: Decimal


async def _stock_values(db: AsyncSession, start: date, end: date) -> tuple[Decimal, Decimal]:
    """The stock's value at the start of ``start`` and at the end of ``end``:
    each item's quantity at its moving average on the day, replayed from its
    adjustments as ``hoard.costing`` derives them."""
    rows = await db.exec(
        select(
            col(Adjustment.item_id),
            col(Adjustment.dated_on),
            col(Adjustment.change),
            col(Adjustment.unit_cost),
        )
        .where(Adjustment.dated_on <= end)
        .order_by(col(Adjustment.item_id), col(Adjustment.dated_on), col(Adjustment.id))
    )
    opening = closing = Decimal(0)
    for _, adjustments in groupby(rows.all(), key=lambda row: row[0]):
        quantity = average = Decimal(0)
        opened: Decimal | None = None
        for _, dated_on, change, unit_cost in adjustments:
            if opened is None and dated_on >= start:
                opened = (quantity * average).quantize(PLACES)
            if change > 0:
                held = max(quantity, Decimal(0))
                average = ((held * average + change * unit_cost) / (held + change)).quantize(PLACES)
            quantity += change
            if quantity <= 0:
                average = Decimal(0)
        value = (quantity * average).quantize(PLACES)
        opening += value if opened is None else opened
        closing += value
    return opening, closing


async def cost_of_goods(db: AsyncSession, start: date, end: date) -> CostOfGoods:
    """What the goods cost from ``start`` to ``end``."""
    opening, closing = await _stock_values(db, start, end)

    def total(*where: Any) -> Any:
        value = func.sum(Adjustment.change * Adjustment.unit_cost).filter(*where)
        return func.coalesce(value, 0)

    starting, purchases, personal_use, shipped = (
        await db.exec(
            select(
                total(Adjustment.kind == AdjustmentKind.STARTING),
                total(Adjustment.kind == AdjustmentKind.PURCHASE),
                -total(
                    Adjustment.kind == AdjustmentKind.MANUAL,
                    AdjustmentReason.key == DefaultReason.PERSONAL_USE.value,
                ),
                -total(col(Adjustment.kind).in_([AdjustmentKind.SHIPMENT, AdjustmentKind.RETURN])),
            )
            .outerjoin(AdjustmentReason, col(AdjustmentReason.id) == Adjustment.reason_id)
            .where(Adjustment.dated_on >= start, Adjustment.dated_on <= end)
        )
    ).one()
    uncosted_runs = (
        await db.exec(
            select(func.count()).where(
                ProductionRun.status == RunStatus.COMPLETED,
                col(ProductionRun.uncosted),
                col(ProductionRun.completed_on) >= start,
                col(ProductionRun.completed_on) <= end,
            )
        )
    ).one()
    starting, purchases, personal_use, shipped = (
        Decimal(amount).quantize(PLACES) for amount in (starting, purchases, personal_use, shipped)
    )
    return CostOfGoods(
        currency=await currency(db),
        opening=opening,
        starting=starting,
        purchases=purchases,
        personal_use=personal_use,
        closing=closing,
        cost_of_goods=opening + starting + purchases - personal_use - closing,
        shipped=shipped,
        uncosted_runs=uncosted_runs,
    )


async def mass_balance(
    db: AsyncSession, start: date, end: date, split_by: int | None = None
) -> list[Balance]:
    """Each item's quantities from ``start`` to ``end``, by name; split by the
    lot property ``split_by``, a select, when one is named. An item that had
    nothing and moved nothing over the period has no row."""

    def moved(*where: Any) -> Any:
        return func.coalesce(func.sum(Adjustment.change).filter(*where), 0)

    within = Adjustment.dated_on >= start
    produced = (within, Adjustment.kind == AdjustmentKind.PRODUCTION)
    received = col(Adjustment.kind).in_([AdjustmentKind.PURCHASE, AdjustmentKind.STARTING])
    sold = col(Adjustment.kind).in_([AdjustmentKind.SHIPMENT, AdjustmentKind.RETURN])
    lost = col(Adjustment.kind).in_([AdjustmentKind.MANUAL, AdjustmentKind.STOCKTAKE])
    if split_by is None:
        option: Any = literal_column("NULL", Text)
        grouped = [col(Adjustment.item_id)]
    else:
        option = col(PropertyValue.value).op("#>>", return_type=Text)(literal_column("'{}'"))
        grouped = [col(Adjustment.item_id), option]
    query = sa_select(
        col(Adjustment.item_id),
        option,
        moved(Adjustment.dated_on < start),
        moved(within, received),
        moved(*produced, col(Adjustment.production_run_line_id).is_(None)),
        -moved(*produced, col(Adjustment.production_run_line_id).is_not(None)),
        -moved(within, sold),
        -moved(within, lost),
        moved(),
    ).where(Adjustment.dated_on <= end)
    if split_by is not None:
        query = query.outerjoin(
            PropertyValue,
            (col(PropertyValue.lot_id) == Adjustment.lot_id)
            & (PropertyValue.definition_id == split_by),
        )
    rows = (await db.execute(query.group_by(*grouped))).all()
    labels = await _labels(db, {row[0] for row in rows})
    balances = [
        Balance(
            item_id,
            *labels[item_id],
            held,
            *(Decimal(amount).quantize(PLACES) for amount in totals),
        )
        for item_id, held, *totals in rows
        if any(totals)
    ]
    return sorted(
        balances,
        key=lambda one: (one.name.casefold(), one.item_id, one.option is None, one.option or ""),
    )


async def _labels(
    db: AsyncSession, item_ids: set[int]
) -> dict[int, tuple[ItemKind, str, dict[str, str], str | None]]:
    """What each item is, is called, and counts its stock in."""
    if not item_ids:
        return {}
    name = func.coalesce(Material.name, Base.name, Product.name)
    rows = await db.exec(
        select(Item, name, col(Variation.attributes), col(Unit.name))
        .outerjoin(Material, col(Material.id) == Item.id)
        .outerjoin(Base, col(Base.id) == Item.id)
        .outerjoin(Variation, col(Variation.id) == Item.id)
        .outerjoin(Product, col(Product.id) == Variation.product_id)
        .outerjoin(
            Unit,
            col(Unit.id) == func.coalesce(Material.tracking_unit_id, Base.tracking_unit_id),
        )
        .where(col(Item.id).in_(item_ids))
    )
    return {
        item.id: (item.kind, item_name, dict(attributes or {}), unit)
        for item, item_name, attributes, unit in rows
    }
