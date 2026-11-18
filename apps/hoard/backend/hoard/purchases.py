"""Purchases: receiving their lines, and what each line cost landed.

A purchase's discount and shipping are spread over its lines by value
(quantity times unit cost). Its tax stays out of every cost, for a business
that claims back the tax it pays on what it buys; one that can't counts it in
cost (``OrganizationSettings.purchase_tax_in_cost``), and its tax is spread
with the shipping. A line's landed cost is its value and its share, per
tracking unit.

A line buys a material or a product's variation, never a base or a bundle's
variation, which has no stock of its own (:func:`bought`). Receiving it makes
its lot, at the purchase's location, and its purchase adjustment, at the
landed cost, both dated the day it arrived; a material that is not
inventoriable gets neither. Costing re-derives the item's average from it, and
a listed variation's new count is pushed to the stores mapping the location
(``hoard.costing``). Receiving part of a line splits that part off as a line of its own
at the same unit cost, the rest staying on order: their quantities split in
proportion (``hoard.units.split``), so their values add up to the line's and
the discount and shipping spread as before. Taking the part back puts it back
into the rest while that is still on order as it was. After a purchase or its
lines are edited, :func:`rederive_purchase` carries the edit into their
adjustments and lots and re-derives each item's cost; after the organization
starts or stops counting tax in cost, :func:`rederive_taxed_purchases` does so
for every purchase that paid tax.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func
from sqlmodel import col, select

from hoard import costing, events, units
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    Lot,
    Material,
    OrganizationSettings,
    Product,
    Purchase,
    PurchaseLine,
    Variation,
)
from hoard.stock import StockError, StockRefused
from seneschal_core.db import OrganizationSession, WriteSession


class PurchaseError(StrEnum):
    """Why a purchase was refused: codes pages translate."""

    #: A cost was sent by a member who does not see costs.
    COSTS_HIDDEN = "COSTS_HIDDEN"
    #: Another purchase has the code.
    PURCHASE_CODE_TAKEN = "PURCHASE_CODE_TAKEN"
    #: The line is received: it can't be received again or buy another item.
    PURCHASE_LINE_RECEIVED = "PURCHASE_LINE_RECEIVED"
    #: The line has not been received.
    PURCHASE_LINE_NOT_RECEIVED = "PURCHASE_LINE_NOT_RECEIVED"
    #: A line would be received before its purchase's date.
    RECEIVED_BEFORE_PURCHASE = "RECEIVED_BEFORE_PURCHASE"
    #: More was received than the line is for.
    RECEIVED_MORE_THAN_LINE = "RECEIVED_MORE_THAN_LINE"
    #: The line names what can't be bought: a base, or a bundle's variation.
    PURCHASE_LINE_KIND = "PURCHASE_LINE_KIND"


class PurchaseRefused(Exception):
    def __init__(self, code: PurchaseError | units.UnitError) -> None:
        super().__init__(code.value)
        self.code = code


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


def landed_costs(
    purchase: Purchase, lines: list[PurchaseLine], *, tax_in_cost: bool
) -> dict[int, Decimal]:
    """What one tracking unit of each of ``lines`` cost, by line id: with a
    share of the purchase's tax when ``tax_in_cost``."""
    values = [line.purchase_quantity * line.unit_cost for line in lines]
    spread = purchase.shipping - purchase.discount + (purchase.tax if tax_in_cost else 0)
    shares = _spread(spread, values)
    return {
        line.id: ((value + share) / line.tracking_quantity).quantize(PLACES)
        for line, value, share in zip(lines, values, shares, strict=True)
    }


async def bought(db: WriteSession, item_id: int) -> Material | Variation:
    """What a line buying ``item_id`` buys: a material, or a variation of a
    product that is not a bundle. Refused for anything else."""
    found = await db.get(Material, item_id) or await db.get(Variation, item_id)
    if found is None or (
        isinstance(found, Variation) and (await db.get_one(Product, found.product_id)).bundle
    ):
        raise PurchaseRefused(PurchaseError.PURCHASE_LINE_KIND)
    return found


async def _lines(db: WriteSession, purchase_id: int) -> list[PurchaseLine]:
    lines = await db.exec(
        select(PurchaseLine)
        .where(PurchaseLine.purchase_id == purchase_id)
        .order_by(col(PurchaseLine.id))
        .execution_options(populate_existing=True)
    )
    return list(lines.all())


async def tax_in_cost(db: OrganizationSession) -> bool:
    """Whether the organization counts the tax it pays on a purchase in what
    the purchase cost."""
    return bool((await db.exec(select(OrganizationSettings.purchase_tax_in_cost))).first())


async def _land(db: WriteSession, purchase: Purchase, taxed: bool) -> set[int]:
    """Carry a purchase's location and lines into its received lines'
    adjustments and lots, at their landed costs and on the days they arrived.
    Answers the items it buys. Refused when a line arrived before the
    purchase's date."""
    await db.flush()
    lines = await _lines(db, purchase.id)
    costs = landed_costs(purchase, lines, tax_in_cost=taxed)
    for line in lines:
        if line.received_on is None:
            continue
        if line.received_on < purchase.dated_on:
            raise PurchaseRefused(PurchaseError.RECEIVED_BEFORE_PURCHASE)
        adjustment = (
            await db.exec(select(Adjustment).where(Adjustment.purchase_line_id == line.id))
        ).first()
        if adjustment is not None:
            adjustment.dated_on = line.received_on
            adjustment.location_id = purchase.location_id
            adjustment.change = line.tracking_quantity
            adjustment.unit_cost = costs[line.id]
        lot = (await db.exec(select(Lot).where(Lot.purchase_line_id == line.id))).first()
        if lot is not None:
            lot.dated_on = line.received_on
            lot.location_id = purchase.location_id
    return {line.item_id for line in lines}


async def rederive_purchase(db: WriteSession, purchase: Purchase) -> None:
    """Carry a purchase's location and lines into its received lines'
    adjustments and lots, at their landed costs and on the days they arrived,
    and re-derive the cost of every item it buys. Refused when a line
    arrived before the purchase's date."""
    for item_id in sorted(await _land(db, purchase, await tax_in_cost(db))):
        await costing.rederive(db, item_id)


async def rederive_taxed_purchases(db: WriteSession) -> None:
    """Land every purchase that paid tax again, as the organization now counts
    tax in cost or not, and re-derive the cost of every item they buy, on
    through the runs that consumed it."""
    await db.flush()
    taxed = await tax_in_cost(db)
    found = await db.exec(select(Purchase).where(Purchase.tax > 0).order_by(col(Purchase.id)))
    items: set[int] = set()
    for purchase in found.all():
        items |= await _land(db, purchase, taxed)
    for item_id in sorted(items):
        await costing.rederive(db, item_id)


async def _split(db: WriteSession, line: PurchaseLine, quantity: Decimal) -> PurchaseLine:
    """``quantity`` tracking units of ``line`` as a line of their own, split
    from it at its unit cost; ``line`` keeps the rest."""
    if quantity > line.tracking_quantity:
        raise PurchaseRefused(PurchaseError.RECEIVED_MORE_THAN_LINE)
    whole = line.tracking_quantity
    try:
        bought, bought_left = units.split(line.purchase_quantity, quantity, whole)
        tracked, tracked_left = units.split(whole, quantity, whole)
    except units.UnitRefused as refused:
        raise PurchaseRefused(refused.code) from None
    line.purchase_quantity, line.tracking_quantity = bought_left, tracked_left
    part = PurchaseLine(
        organization_id=db.organization_id,
        purchase_id=line.purchase_id,
        item_id=line.item_id,
        purchase_quantity=bought,
        tracking_quantity=tracked,
        unit_cost=line.unit_cost,
        split_from_id=line.id,
    )
    db.add(part)
    await db.flush()
    return part


async def receive_line(
    db: WriteSession,
    line: PurchaseLine,
    *,
    received_on: date | None = None,
    quantity: Decimal | None = None,
    number: str | None = None,
    expires_on: date | None = None,
    notes: str | None = None,
) -> PurchaseLine:
    """Receive ``line`` on ``received_on``, today when not given: for a
    variation or an inventoriable material, its lot (numbered from the
    purchase's code and the line when no number is given) and its purchase
    adjustment. A ``quantity`` less than the line's tracking quantity receives
    only that much, split off as a line of its own. Records the purchase
    received once every line is. Answers the line received."""
    purchase = await db.get_one(Purchase, line.purchase_id)
    item = await bought(db, line.item_id)
    received_on = received_on or date.today()
    if received_on < purchase.dated_on:
        raise PurchaseRefused(PurchaseError.RECEIVED_BEFORE_PURCHASE)
    if quantity is not None and quantity != line.tracking_quantity:
        line = await _split(db, line, quantity)
    line.received_on = received_on
    if isinstance(item, Variation) or item.inventoriable:
        lot = Lot(
            organization_id=db.organization_id,
            item_id=item.id,
            location_id=purchase.location_id,
            purchase_line_id=line.id,
            number=number or f"{purchase.code}-{line.id}",
            dated_on=received_on,
            expires_on=expires_on,
            notes=notes,
        )
        db.add(lot)
        await db.flush()
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=item.id,
                location_id=purchase.location_id,
                lot_id=lot.id,
                kind=AdjustmentKind.PURCHASE,
                dated_on=received_on,
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
    return line


async def _unreceive(db: WriteSession, line: PurchaseLine) -> None:
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
    line.received_on = None
    await costing.rederive(db, line.item_id)


async def unreceive_line(db: WriteSession, line: PurchaseLine) -> None:
    """Take back what ``line`` received (refused while anything draws on its
    lot). A part received from another line goes back into it while that is
    still on order for the same item at the same unit cost; otherwise it
    stays a line of its own, on order."""
    await _unreceive(db, line)
    rest = None if line.split_from_id is None else await db.get(PurchaseLine, line.split_from_id)
    if (
        rest is None
        or rest.received_on is not None
        or (rest.item_id, rest.unit_cost) != (line.item_id, line.unit_cost)
    ):
        return
    rest.purchase_quantity += line.purchase_quantity
    rest.tracking_quantity += line.tracking_quantity
    await db.delete(line)
    await rederive_purchase(db, await db.get_one(Purchase, rest.purchase_id))


async def delete_line(db: WriteSession, line: PurchaseLine) -> None:
    """Delete ``line``, unreceiving it first, and re-derive what the rest of
    its purchase cost."""
    if line.received_on is not None:
        await _unreceive(db, line)
    purchase = await db.get_one(Purchase, line.purchase_id)
    await db.delete(line)
    await rederive_purchase(db, purchase)


async def delete_purchase(db: WriteSession, purchase: Purchase) -> None:
    """Delete ``purchase``, unreceiving its lines first."""
    for line in await _lines(db, purchase.id):
        if line.received_on is not None:
            await _unreceive(db, line)
    await db.delete(purchase)
    await db.flush()


def overdue(purchase: Purchase, on_order: bool, today: date | None = None) -> bool:
    """Whether ``purchase`` is past the day it was expected, as it stands
    ``today``, with lines still ``on_order``."""
    return (
        on_order
        and purchase.expected_on is not None
        and purchase.expected_on < (today or date.today())
    )
