"""Shipments: some of an order's lines sent from a location, and what comes
back of them.

A shipment's lines name the lot each draws on: the lot someone recorded, for
instance by scanning it, or else the one Hoard assumes when it ships, as a
production run does (``hoard.stock.draw_lots``), the line split over as many
lots as it takes and marked assumed. Only shipping it moves stock: each line
out of its lot, costed at the item's average on the day it shipped
(``hoard.costing``). Unshipping it takes those adjustments back and returns
its assumed lines to naming no lot.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func
from sqlmodel import col, select

from hoard import costing
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    ReturnLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)
from hoard.stock import draw_lots
from seneschal_core.db import WriteSession


class ShipmentError(StrEnum):
    """Why a change to a shipment was refused: codes pages translate."""

    #: It has shipped: what it moved changes only once it is unshipped.
    SHIPMENT_SHIPPED = "SHIPMENT_SHIPPED"
    #: It has not shipped, so there is nothing to take back.
    SHIPMENT_NOT_SHIPPED = "SHIPMENT_NOT_SHIPPED"
    #: Something came back of it.
    SHIPMENT_RETURNED = "SHIPMENT_RETURNED"


class ShipmentRefused(Exception):
    def __init__(self, code: ShipmentError) -> None:
        super().__init__(code.value)
        self.code = code


async def lines(db: WriteSession, shipment_id: int) -> list[ShipmentLine]:
    """A shipment's lines, in the order they were added."""
    found = await db.exec(
        select(ShipmentLine)
        .where(ShipmentLine.shipment_id == shipment_id)
        .order_by(col(ShipmentLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _assume_lots(db: WriteSession, shipment: Shipment) -> list[ShipmentLine]:
    """Assume a lot for each line that names none, splitting a line over as
    many lots as it takes. Answers the lines."""
    found = await lines(db, shipment.id)
    drawn: dict[int, Decimal] = defaultdict(Decimal)
    for line in found:
        if line.lot_id is not None:
            drawn[line.lot_id] += line.quantity
    picked: list[ShipmentLine] = []
    for line in found:
        if line.lot_id is not None:
            picked.append(line)
            continue
        current: ShipmentLine | None = line
        for lot_id, take in await draw_lots(
            db, line.item_id, shipment.location_id, line.quantity, drawn
        ):
            if current is None:
                current = ShipmentLine(
                    organization_id=db.organization_id,
                    shipment_id=shipment.id,
                    order_line_id=line.order_line_id,
                    item_id=line.item_id,
                )
                db.add(current)
            current.lot_id, current.quantity, current.assumed = lot_id, take, lot_id is not None
            picked.append(current)
            current = None
    await db.flush()
    return picked


async def post(db: WriteSession, shipment: Shipment, shipped_on: date) -> None:
    """Ship ``shipment`` on ``shipped_on``: each line out of its lot, assuming
    a lot for each that names none, and each item's cost re-derived."""
    if shipment.status is ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_SHIPPED)
    shipped = await _assume_lots(db, shipment)
    for line in shipped:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=line.item_id,
                location_id=shipment.location_id,
                lot_id=line.lot_id,
                kind=AdjustmentKind.SHIPMENT,
                dated_on=shipped_on,
                change=-line.quantity,
                shipment_line_id=line.id,
            )
        )
    shipment.status = ShipmentStatus.SHIPPED
    shipment.shipped_on = shipped_on
    await db.flush()
    for item_id in sorted({line.item_id for line in shipped}):
        await costing.rederive(db, item_id)


async def unpost(db: WriteSession, shipment: Shipment) -> None:
    """Take back what a shipped shipment moved: its adjustments go, its
    assumed lines name no lot again, and it is pending. Refused once something
    has come back of it."""
    if shipment.status is not ShipmentStatus.SHIPPED:
        raise ShipmentRefused(ShipmentError.SHIPMENT_NOT_SHIPPED)
    shipped = await lines(db, shipment.id)
    returned = await db.exec(
        select(func.count())
        .select_from(ReturnLine)
        .where(col(ReturnLine.shipment_line_id).in_([line.id for line in shipped]))
    )
    if returned.one():
        raise ShipmentRefused(ShipmentError.SHIPMENT_RETURNED)
    await db.execute(
        delete(Adjustment).where(
            col(Adjustment.shipment_line_id).in_([line.id for line in shipped])
        )
    )
    # Each order line's assumed lines become one line again, naming no lot.
    kept: dict[int, ShipmentLine] = {}
    for line in shipped:
        if line.lot_id is not None and not line.assumed:
            continue
        if line.order_line_id in kept:
            kept[line.order_line_id].quantity += line.quantity
            await db.delete(line)
        else:
            line.lot_id, line.assumed = None, False
            kept[line.order_line_id] = line
    shipment.status = ShipmentStatus.PENDING
    shipment.shipped_on = None
    await db.flush()
    for item_id in sorted({line.item_id for line in shipped}):
        await costing.rederive(db, item_id)


async def record_return(
    db: WriteSession, line: ShipmentLine, quantity: Decimal, dated_on: date, *, damaged: bool
) -> ReturnLine:
    """Record ``quantity`` of a shipped line coming back on ``dated_on``: in,
    at what it shipped at, or, ``damaged``, written off as returned damaged."""
    raise NotImplementedError
