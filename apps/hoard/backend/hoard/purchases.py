"""Purchases: receiving their lines, and what each line cost landed.

A purchase's discount and shipping are spread over its lines by value
(quantity times unit cost); its tax stays out of every cost. A line's landed
cost is its value and its share, per tracking unit.

Receiving a line of an inventoriable material makes its lot, at the purchase's
location, and its purchase adjustment, at the landed cost. After a purchase or
its lines are edited, :func:`rederive_purchase` carries the edit into their
adjustments and lots and re-derives each material's cost.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func
from sqlmodel import col, select

from hoard import costing, events
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    Lot,
    Material,
    Purchase,
    PurchaseLine,
)
from hoard.stock import StockError, StockRefused
from seneschal_core.db import WriteSession


def _spread(amount: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``amount`` shared over ``weights`` in proportion, to six places, the
    last share taking what rounding leaves. Equal weights when they sum to zero."""
    if not weights:
        return []
    total = sum(weights, Decimal(0))
    if total == 0:
        weights, total = [Decimal(1)] * len(weights), Decimal(len(weights))
    shares = [(amount * weight / total).quantize(PLACES) for weight in weights[:-1]]
    return [*shares, amount - sum(shares, Decimal(0))]


def landed_costs(purchase: Purchase, lines: list[PurchaseLine]) -> dict[int, Decimal]:
    """What one tracking unit of each of ``lines`` cost, by line id."""
    values = [line.purchase_quantity * line.unit_cost for line in lines]
    shares = _spread(purchase.shipping - purchase.discount, values)
    return {
        line.id: ((value + share) / line.tracking_quantity).quantize(PLACES)
        for line, value, share in zip(lines, values, shares, strict=True)
    }


async def _lines(db: WriteSession, purchase_id: int) -> list[PurchaseLine]:
    lines = await db.exec(
        select(PurchaseLine)
        .where(PurchaseLine.purchase_id == purchase_id)
        .order_by(col(PurchaseLine.id))
        .execution_options(populate_existing=True)
    )
    return list(lines.all())


async def rederive_purchase(db: WriteSession, purchase: Purchase) -> None:
    """Carry a purchase's date, location and lines into its received lines'
    adjustments and lots, at their landed costs, and re-derive the cost of
    every material it buys."""
    await db.flush()
    lines = await _lines(db, purchase.id)
    costs = landed_costs(purchase, lines)
    for line in lines:
        adjustment = (
            await db.exec(select(Adjustment).where(Adjustment.purchase_line_id == line.id))
        ).first()
        if adjustment is not None:
            adjustment.dated_on = purchase.dated_on
            adjustment.location_id = purchase.location_id
            adjustment.change = line.tracking_quantity
            adjustment.unit_cost = costs[line.id]
        lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == line.id))).first()
        if lot is not None:
            lot.dated_on = purchase.dated_on
            lot.location_id = purchase.location_id
    for material_id in sorted({line.material_id for line in lines}):
        await costing.rederive(db, material_id)


async def receive_line(
    db: WriteSession,
    line: PurchaseLine,
    *,
    number: str | None = None,
    expires_on: date | None = None,
    notes: str | None = None,
) -> Lot | None:
    """Receive ``line``: for an inventoriable material, its lot (numbered from
    the purchase's code when no number is given) and its purchase adjustment.
    Records the line received, and the purchase received once every line is.
    Answers the lot."""
    purchase = await db.get_one(Purchase, line.purchase_id)
    material = await db.get_one(Material, line.material_id)
    line.received = True
    lot = None
    if material.inventoriable:
        lot = Lot(
            organization_id=db.organization_id,
            item_id=material.id,
            location_id=purchase.location_id,
            purchase_line_id=line.id,
            number=number or f"{purchase.code}-{line.id}",
            dated_on=purchase.dated_on,
            expires_on=expires_on,
            notes=notes,
        )
        db.add(lot)
        await db.flush()
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=material.id,
                location_id=purchase.location_id,
                lot_id=lot.id,
                kind=AdjustmentKind.PURCHASE,
                dated_on=purchase.dated_on,
                change=line.tracking_quantity,
                purchase_line_id=line.id,
            )
        )
    await rederive_purchase(db, purchase)
    received = await events.purchase_event(
        db, events.EventName.PURCHASE_LINE_RECEIVED, purchase, line
    )
    await events.record(db, received)
    if received.payload["lines_received"] == received.payload["lines"]:
        await events.record(
            db, await events.purchase_event(db, events.EventName.PURCHASE_RECEIVED, purchase)
        )
    return lot


async def unreceive_line(db: WriteSession, line: PurchaseLine) -> None:
    """Undo receiving ``line``: its purchase adjustment and its lot go. Refused
    while any other adjustment draws on the lot."""
    await db.flush()
    lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == line.id))).first()
    if lot is not None:
        others = await db.exec(
            select(func.count())
            .select_from(Adjustment)
            .where(
                Adjustment.lot_id == lot.id,
                col(Adjustment.purchase_line_id).is_distinct_from(line.id),
            )
        )
        if others.one():
            raise StockRefused(StockError.LOT_IN_USE)
    await db.execute(delete(Adjustment).where(col(Adjustment.purchase_line_id) == line.id))
    await db.execute(delete(Lot).where(col(Lot.purchase_line_id) == line.id))
    line.received = False
    await costing.rederive(db, line.material_id)


async def delete_line(db: WriteSession, line: PurchaseLine) -> None:
    """Delete ``line``, unreceiving it first, and re-derive what the rest of
    its purchase cost."""
    if line.received:
        await unreceive_line(db, line)
    purchase = await db.get_one(Purchase, line.purchase_id)
    await db.delete(line)
    await rederive_purchase(db, purchase)


async def delete_purchase(db: WriteSession, purchase: Purchase) -> None:
    """Delete ``purchase``, unreceiving its lines first."""
    for line in await _lines(db, purchase.id):
        if line.received:
            await unreceive_line(db, line)
    await db.delete(purchase)
    await db.flush()
