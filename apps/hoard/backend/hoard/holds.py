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

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from hoard.records import Hold, Lot
from seneschal_core.db import WriteSession
from seneschal_core.platform import EndpointCall


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
) -> Hold:
    """Put ``lot`` on hold for a reason, ready on ``ready_on`` when given."""
    raise NotImplementedError


async def release(db: WriteSession, hold: Hold, ended_on: date) -> None:
    """End ``hold`` on ``ended_on``, its lot available again."""
    raise NotImplementedError


async def discard(
    db: WriteSession, hold: Hold, reason_id: int, ended_on: date, quantity: Decimal | None = None
) -> None:
    """Discard ``quantity`` of a held lot, or all of it, with a manual
    adjustment naming ``hold`` and the adjustment reason ``reason_id``, and
    end the hold on ``ended_on``: discarded, or released when some is left."""
    raise NotImplementedError


async def upkeep(db: WriteSession) -> None:
    """Record ``hold-ready`` once for each open hold whose ready date has come."""


async def ready(call: EndpointCall) -> dict[str, Any]:
    """Answer ``holds-ready``: the open holds whose ready date has come."""
    raise NotImplementedError
