"""Holds: a lot kept from being used, shipped or offered to a store, for a
reason among the organization's hold reasons, and perhaps until a ready date.

A lot has at most one open hold. Drawing stock skips a held lot
(``hoard.stock.draw_lots``), and what is available of an item leaves out what
its held lots hold (``hoard.orders.available``). A hold ends one of two ways:
released, or discarded, whole or in part, by a manual adjustment that names
it and its reason. A completed run whose item cures puts its lot on hold,
curing, ready on the day its cure time sets; ``upkeep`` records
``hold-ready`` once for each hold whose day has come. Starting or ending a
hold notes its item for its listings (:func:`note`).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Base,
    DefaultHoldReason,
    Hold,
    HoldEnd,
    HoldReason,
    Item,
    Lot,
    Order,
    Picking,
    Product,
    ProductionRun,
    Shipment,
    ShipmentLine,
    Variation,
)
from seneschal_core.db import WriteSession
from seneschal_core.platform import EndpointCall, organization_session


def held_lots() -> SelectOfScalar[int]:
    """The ids of the lots on hold, to filter a query by."""
    return select(Hold.lot_id).where(col(Hold.ended).is_(None))


async def held_lot_ids(
    db: AsyncSession, item_id: int | None = None, location_id: int | None = None
) -> set[int]:
    """The lots on hold: of one item, and at one location, when named."""
    query = select(Lot.id).where(col(Lot.id).in_(held_lots()))
    if item_id is not None:
        query = query.where(Lot.item_id == item_id)
    if location_id is not None:
        query = query.where(Lot.location_id == location_id)
    return set((await db.exec(query)).all())


async def on_hold(db: AsyncSession, item_id: int, location_id: int | None = None) -> Decimal:
    """What an item's held lots hold: at one location, or at all of them."""
    query = select(func.coalesce(func.sum(func.greatest(Lot.balance, 0)), 0)).where(
        Lot.item_id == item_id, col(Lot.id).in_(held_lots())
    )
    if location_id is not None:
        query = query.where(Lot.location_id == location_id)
    return Decimal((await db.exec(query)).one())


async def note(db: WriteSession, lot_id: int) -> None:
    """Note that a lot's hold started or ended, so that its item's listings
    are pushed what is now available."""
    # Imported here: listings read what is available through hoard.orders, which reads this.
    from hoard import listings

    lot = await db.get_one(Lot, lot_id)
    await listings.note_changed(db, lot.item_id)


class HoldError(StrEnum):
    """Why a change to a hold, or a draw on a lot, was refused: codes pages translate."""

    #: The lot is on hold, so nothing draws on it and it is not put on hold again.
    LOT_ON_HOLD = "LOT_ON_HOLD"
    #: Its item's category records every lot drawn, and the line names none.
    LOT_REQUIRED = "LOT_REQUIRED"
    #: The hold has ended.
    HOLD_ENDED = "HOLD_ENDED"
    #: The hold's ready date has not come, so its lot is not released yet.
    HOLD_NOT_READY = "HOLD_NOT_READY"
    #: More is discarded than the lot holds.
    DISCARD_OVER_BALANCE = "DISCARD_OVER_BALANCE"


class HoldRefused(Exception):
    def __init__(self, code: HoldError) -> None:
        super().__init__(code.value)
        self.code = code


async def refusal(
    db: AsyncSession, draws: Iterable[tuple[int, int | None]], *, named: bool = True
) -> HoldError | None:
    """Why draws of ``(item_id, lot_id)`` may not be made: a lot on hold, or,
    where every lot must be ``named``, a draw naming none of an item whose
    category's rule records every lot (:class:`~hoard.records.Picking`)."""
    # Imported here: stock filters its draws through this module.
    from hoard.stock import picking

    draws = list(draws)
    lot_ids = {lot_id for _, lot_id in draws if lot_id is not None}
    if lot_ids:
        held = await db.exec(select(Hold.id).where(col(Hold.lot_id).in_(lot_ids), _open()))
        if held.first() is not None:
            return HoldError.LOT_ON_HOLD
    if named:
        for item_id in sorted({item_id for item_id, lot_id in draws if lot_id is None}):
            if await picking(db, item_id) is Picking.RECORDED:
                return HoldError.LOT_REQUIRED
    return None


def _open() -> ColumnElement[bool]:
    return col(Hold.ended).is_(None)


async def reason_id(db: WriteSession, key: DefaultHoldReason) -> int:
    """The id of the default hold reason ``key``, made again if it was removed."""
    await db.execute(
        insert(HoldReason)
        .values(organization_id=db.organization_id, key=key.value)
        .on_conflict_do_nothing()
    )
    return (await db.exec(select(HoldReason.id).where(HoldReason.key == key.value))).one()


async def _record(db: WriteSession, name: str, hold: Hold) -> None:
    """Record the hold event ``name`` about ``hold``."""
    # Imported here: events read stock, which reads this module.
    from hoard import events

    await events.record(db, await events.hold_event(db, events.EventName(name), hold))


async def place(
    db: WriteSession,
    lot: Lot,
    reason_id: int,
    *,
    ready_on: date | None = None,
    notes: str | None = None,
    run_id: int | None = None,
    order_id: int | None = None,
    from_hold_id: int | None = None,
    quiet: bool = False,
) -> Hold:
    """Put ``lot`` on hold for a reason, ready on ``ready_on`` when given,
    unless it is on hold already. A ``quiet`` hold records no event."""
    if (await db.exec(select(Hold.id).where(Hold.lot_id == lot.id, _open()))).first():
        raise HoldRefused(HoldError.LOT_ON_HOLD)
    hold = Hold(
        organization_id=db.organization_id,
        lot_id=lot.id,
        reason_id=reason_id,
        ready_on=ready_on,
        notes=notes,
        run_id=run_id,
        order_id=order_id,
        from_hold_id=from_hold_id,
    )
    db.add(hold)
    await db.flush()
    if not quiet:
        await _record(db, "hold-placed", hold)
    await note(db, lot.id)
    return hold


async def place_made_from(db: WriteSession, hold: Hold) -> list[Hold]:
    """Put on hold, for ``hold``'s reason, every lot still holding stock that
    a forward trace from its lot reaches, recorded or assumed
    (:func:`hoard.trace.forward`), unless it is on hold already. Answers the
    holds placed."""
    # Imported here: tracing reads the runs and shipments, which read this module.
    from hoard import trace

    reached = (await trace.forward(db, hold.lot_id)).lot_ids()
    if not reached:
        return []
    found = await db.exec(
        select(Lot)
        .where(col(Lot.id).in_(reached), col(Lot.balance) > 0, col(Lot.id).not_in(held_lots()))
        .order_by(col(Lot.id))
    )
    return [
        await place(db, lot, hold.reason_id, notes=hold.notes, from_hold_id=hold.id)
        for lot in found.all()
    ]


async def place_from_order(
    db: WriteSession, order: Order, reason_id: int, *, notes: str | None, made_from: bool
) -> list[Hold]:
    """Put on hold every lot ``order``'s shipments drew, recorded or assumed,
    for a problem reported with the order, and, when ``made_from``,
    everything made from each of them. Lots on hold already are left as they
    are. Answers the holds placed."""
    drawn = (
        select(ShipmentLine.lot_id)
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .where(Shipment.order_id == order.id, col(ShipmentLine.lot_id).is_not(None))
    )
    found = await db.exec(
        select(Lot)
        .where(col(Lot.id).in_(drawn), col(Lot.id).not_in(held_lots()))
        .order_by(col(Lot.id))
    )
    placed: list[Hold] = []
    for lot in found.all():
        hold = await place(db, lot, reason_id, notes=notes, order_id=order.id)
        placed.append(hold)
        if made_from:
            placed += await place_made_from(db, hold)
    return placed


async def cure(db: WriteSession, run: ProductionRun, *, quiet: bool = False) -> Hold | None:
    """Put a completed run's lot on hold, curing, ready on the day its item's
    cure time sets, when its item cures and the run made something. A
    ``quiet`` hold records no event."""
    days = (
        await db.exec(
            select(func.coalesce(Base.cure_days, Product.cure_days))
            .select_from(Item)
            .outerjoin(Base, col(Base.id) == Item.id)
            .outerjoin(Variation, col(Variation.id) == Item.id)
            .outerjoin(Product, col(Product.id) == Variation.product_id)
            .where(Item.id == run.item_id)
        )
    ).first()
    if not days or run.output_lot_id is None or not run.actual_quantity or not run.completed_on:
        return None
    lot = await db.get_one(Lot, run.output_lot_id)
    return await place(
        db,
        lot,
        await reason_id(db, DefaultHoldReason.CURING),
        ready_on=run.completed_on + timedelta(days=days),
        run_id=run.id,
        quiet=quiet,
    )


def _open_hold(hold: Hold) -> None:
    if hold.ended is not None:
        raise HoldRefused(HoldError.HOLD_ENDED)


def _ready(hold: Hold, ended_on: date) -> None:
    if hold.ready_on is not None and ended_on < hold.ready_on:
        raise HoldRefused(HoldError.HOLD_NOT_READY)


async def _end(db: WriteSession, hold: Hold, ended: HoldEnd, ended_on: date) -> None:
    hold.ended, hold.ended_on = ended, ended_on
    db.add(hold)
    await db.flush()
    await _record(db, "hold-ended", hold)
    await note(db, hold.lot_id)


async def release(db: WriteSession, hold: Hold, ended_on: date) -> None:
    """End ``hold`` on ``ended_on``, its lot available again: not before its
    ready date."""
    _open_hold(hold)
    _ready(hold, ended_on)
    await _end(db, hold, HoldEnd.RELEASED, ended_on)


async def discard(
    db: WriteSession, hold: Hold, reason_id: int, ended_on: date, quantity: Decimal | None = None
) -> None:
    """Discard ``quantity`` of a held lot, or all of it, with a manual
    adjustment naming ``hold`` and the adjustment reason ``reason_id``, and
    end the hold on ``ended_on``: discarded, or released when some is left,
    which waits for its ready date as a release does. What is discarded
    leaves at the item's average cost (``hoard.costing``)."""
    # Imported here: costing reads stock, which reads this module.
    from hoard import costing

    _open_hold(hold)
    lot = await db.get_one(Lot, hold.lot_id, populate_existing=True)
    balance = max(lot.balance, Decimal(0))
    quantity = balance if quantity is None else quantity
    if quantity < 0 or quantity > balance:
        raise HoldRefused(HoldError.DISCARD_OVER_BALANCE)
    left = balance - quantity
    if left > 0:
        _ready(hold, ended_on)
    if quantity > 0:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=lot.item_id,
                location_id=lot.location_id,
                lot_id=lot.id,
                kind=AdjustmentKind.MANUAL,
                dated_on=ended_on,
                change=-quantity,
                reason_id=reason_id,
                hold_id=hold.id,
            )
        )
        await costing.rederive(db, lot.item_id)
    await _end(db, hold, HoldEnd.RELEASED if left > 0 else HoldEnd.DISCARDED, ended_on)


def _due(today: date) -> SelectOfScalar[Hold]:
    """The open holds whose ready date has come by ``today``, the earliest first."""
    return (
        select(Hold)
        .where(_open(), col(Hold.ready_on).is_not(None), col(Hold.ready_on) <= today)
        .order_by(col(Hold.ready_on), col(Hold.id))
    )


async def upkeep(db: WriteSession, today: date | None = None) -> None:
    """Record ``hold-ready`` once for each open hold whose ready date has come."""
    found = await db.exec(_due(today or date.today()).where(col(Hold.ready_sent_at).is_(None)))
    for hold in found.all():
        hold.ready_sent_at = datetime.now(UTC)
        db.add(hold)
        await _record(db, "hold-ready", hold)
    await db.flush()


async def ready(call: EndpointCall) -> dict[str, Any]:
    """Answer ``holds-ready``: the open holds whose ready date has come, the
    earliest first. A reason nobody has named is answered by its key."""
    # Imported here: events read stock, which reads this module.
    from hoard.events import item_name

    columns: dict[str, list[Any]] = {"lot": [], "item": [], "reason": [], "ready_on": []}
    async with organization_session(call) as db:
        for hold in (await db.exec(_due(date.today()))).all():
            lot = await db.get_one(Lot, hold.lot_id)
            reason = await db.get_one(HoldReason, hold.reason_id)
            columns["lot"].append(lot.number)
            columns["item"].append(await item_name(db, lot.item_id))
            columns["reason"].append(reason.name or reason.key)
            columns["ready_on"].append(str(hold.ready_on))
    return columns
