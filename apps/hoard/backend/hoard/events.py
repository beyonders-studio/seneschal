"""The events Hoard sends Initiative, which a community's automations
subscribe to: a production run started or completed, a purchase placed, a
purchase line received, a purchase received in full, stock falling to its
low-stock limit, and a lot put on hold, ready to check, or off hold.

An event is recorded in the outbox (:func:`record`) in the same transaction as
the change that caused it, so it is sent if and only if the change is kept.
``hoard.tracking`` sends what the outbox holds once the transaction commits.

Each event's payload is the returns its emission declares in
``hoard.plugin_manifest``, from :data:`RETURNS`: what the run, purchase or hold is,
where it stands, and the task that tracks it, never a cost. Its identity names
the run, the purchase, the item or the lot it is about, as the writes about
the same record do.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    PLACES,
    Base,
    Hold,
    HoldReason,
    Item,
    ItemKind,
    Lot,
    Material,
    OutboxEvent,
    Product,
    ProductionRun,
    ProductStatus,
    Purchase,
    PurchaseLine,
    StockLevel,
    TaskLink,
    Unit,
    Variation,
)
from hoard.stock import on_hand
from seneschal_core.db import WriteSession


class EventName(StrEnum):
    RUN_STARTED = "run-started"
    RUN_COMPLETED = "run-completed"
    PURCHASE_PLACED = "purchase-placed"
    PURCHASE_LINE_RECEIVED = "purchase-line-received"
    PURCHASE_RECEIVED = "purchase-received"
    STOCK_LOW = "stock-low"
    HOLD_PLACED = "hold-placed"
    #: A hold's ready date has come: its lot is ready to check.
    HOLD_READY = "hold-ready"
    HOLD_ENDED = "hold-ended"


@dataclass(frozen=True)
class Return:
    """One value of an event's payload: its key, its type in the plug-in
    contract's words, and its label."""

    key: str
    type: str
    label: str


@dataclass(frozen=True)
class Identity:
    """What an event is about: a kind of record, and the returns naming it."""

    kind: str
    key: tuple[str, ...]


_TRACKED = [
    Return("initiative_id", "int", "Initiative"),
    Return("task_id", "int", "Task tracking it"),
]
_RUN = [
    Return("run_id", "int", "Production run"),
    Return("code", "string", "Code"),
    Return("status", "string", "Status"),
    Return("item_id", "int", "What it makes"),
    Return("item", "string", "What it makes, by name"),
    Return("planned_batches", "string", "Planned batches"),
    Return("actual_quantity", "string", "Actual quantity"),
    Return("assignee", "string", "Assignee"),
    Return("started_on", "string", "Start date"),
    Return("deadline_on", "string", "Deadline"),
    Return("completed_on", "string", "Completed on"),
    *_TRACKED,
]
_PURCHASE = [
    Return("purchase_id", "int", "Purchase"),
    Return("code", "string", "Code"),
    Return("dated_on", "string", "Date"),
    Return("supplier_contact_id", "int", "Supplier"),
    Return("lines", "int", "Lines"),
    Return("lines_received", "int", "Lines received"),
    *_TRACKED,
]
_HOLD = [
    Return("hold_id", "int", "Hold"),
    Return("lot_id", "int", "Lot"),
    Return("lot", "string", "Lot number"),
    Return("item_id", "int", "Item"),
    Return("item", "string", "Item, by name"),
    Return("reason", "string", "Reason"),
    Return("ready_on", "string", "Ready on"),
    Return("ended", "string", "Released or discarded"),
    Return("ended_on", "string", "Ended on"),
]

#: Each event's payload, in order.
RETURNS: dict[EventName, list[Return]] = {
    EventName.RUN_STARTED: _RUN,
    EventName.RUN_COMPLETED: _RUN,
    EventName.PURCHASE_PLACED: _PURCHASE,
    EventName.PURCHASE_LINE_RECEIVED: [
        *_PURCHASE,
        Return("line_id", "int", "Purchase line"),
        Return("material", "string", "Material"),
        Return("quantity", "string", "Quantity received"),
    ],
    EventName.PURCHASE_RECEIVED: _PURCHASE,
    EventName.STOCK_LOW: [
        Return("kind", "string", "Material, base or product"),
        Return("id", "int", "Its id"),
        Return("name", "string", "Name"),
        Return("unit", "string", "Unit"),
        Return("on_hand", "string", "On hand"),
        Return("low_stock_limit", "string", "Low-stock limit"),
    ],
    EventName.HOLD_PLACED: _HOLD,
    EventName.HOLD_READY: _HOLD,
    EventName.HOLD_ENDED: _HOLD,
}

_RUN_IDENTITY = Identity("run", ("run_id",))
_PURCHASE_IDENTITY = Identity("purchase", ("purchase_id",))
_LOT_IDENTITY = Identity("lot", ("lot_id",))

#: What each event is about.
IDENTITIES: dict[EventName, Identity] = {
    EventName.RUN_STARTED: _RUN_IDENTITY,
    EventName.RUN_COMPLETED: _RUN_IDENTITY,
    EventName.PURCHASE_PLACED: _PURCHASE_IDENTITY,
    EventName.PURCHASE_LINE_RECEIVED: _PURCHASE_IDENTITY,
    EventName.PURCHASE_RECEIVED: _PURCHASE_IDENTITY,
    EventName.STOCK_LOW: Identity("stock", ("kind", "id")),
    EventName.HOLD_PLACED: _LOT_IDENTITY,
    EventName.HOLD_READY: _LOT_IDENTITY,
    EventName.HOLD_ENDED: _LOT_IDENTITY,
}


@dataclass(frozen=True)
class Event:
    """An event to send: its name, its payload, and the initiative it is about."""

    name: EventName
    payload: dict[str, Any]
    initiative_id: int | None = None


async def record(db: WriteSession, event: Event) -> OutboxEvent:
    """Keep ``event`` in the outbox, in ``db``'s transaction."""
    row = OutboxEvent(
        organization_id=db.organization_id,
        name=event.name.value,
        payload=event.payload,
        initiative_id=event.initiative_id,
    )
    db.add(row)
    await db.flush()
    return row


def _text(value: object) -> str | None:
    return None if value is None else str(value)


async def item_name(db: AsyncSession, item_id: int) -> str:
    """What an item is called: a material's or a base's name, or a
    variation's product's name with its attributes."""
    for kind in (Material, Base):
        found = await db.get(kind, item_id)
        if found is not None:
            return found.name
    variation = await db.get_one(Variation, item_id)
    product = await db.get_one(Product, variation.product_id)
    return " ".join([product.name, *variation.attributes.values()])


async def _task(db: AsyncSession, **record: int) -> int | None:
    [(column, record_id)] = record.items()
    found = await db.exec(select(TaskLink.task_id).where(getattr(TaskLink, column) == record_id))
    return found.first()


async def run_event(db: AsyncSession, name: EventName, run: ProductionRun) -> Event:
    """``name``, a run's event, about ``run`` as it stands."""
    payload = {
        "run_id": run.id,
        "code": run.code,
        "status": run.status.value,
        "item_id": run.item_id,
        "item": await item_name(db, run.item_id),
        "planned_batches": str(run.planned_batches),
        "actual_quantity": _text(run.actual_quantity),
        "assignee": run.assignee_ref,
        "started_on": _text(run.started_on),
        "deadline_on": _text(run.deadline_on),
        "completed_on": _text(run.completed_on),
        "initiative_id": run.initiative_id,
        "task_id": await _task(db, run_id=run.id),
    }
    return Event(name, payload, run.initiative_id)


async def purchase_event(
    db: AsyncSession, name: EventName, purchase: Purchase, line: PurchaseLine | None = None
) -> Event:
    """``name``, a purchase's event, about ``purchase`` as it stands, and the
    ``line`` received when it is about one."""
    lines = (
        await db.exec(
            select(func.count(), func.count().filter(col(PurchaseLine.received))).where(
                PurchaseLine.purchase_id == purchase.id
            )
        )
    ).one()
    payload: dict[str, Any] = {
        "purchase_id": purchase.id,
        "code": purchase.code,
        "dated_on": str(purchase.dated_on),
        "supplier_contact_id": purchase.supplier_contact_id,
        "lines": lines[0],
        "lines_received": lines[1],
        "initiative_id": purchase.initiative_id,
        "task_id": await _task(db, purchase_id=purchase.id),
    }
    if line is not None:
        payload |= {
            "line_id": line.id,
            "material": await item_name(db, line.material_id),
            "quantity": str(line.tracking_quantity),
        }
    return Event(name, payload, purchase.initiative_id)


async def hold_event(db: AsyncSession, name: EventName, hold: Hold) -> Event:
    """``name``, a hold's event, about ``hold`` as it stands. A reason nobody
    has named is sent as its key."""
    lot = await db.get_one(Lot, hold.lot_id)
    reason = await db.get_one(HoldReason, hold.reason_id)
    payload = {
        "hold_id": hold.id,
        "lot_id": lot.id,
        "lot": lot.number,
        "item_id": lot.item_id,
        "item": await item_name(db, lot.item_id),
        "reason": reason.name or reason.key,
        "ready_on": _text(hold.ready_on),
        "ended": None if hold.ended is None else hold.ended.value,
        "ended_on": _text(hold.ended_on),
    }
    return Event(name, payload)


async def record_if_low(db: WriteSession, item_id: int) -> None:
    """Record :attr:`EventName.STOCK_LOW` when an item's stock has just fallen
    to its low-stock limit or below: a material's or a base's own, or a
    variation's product's, counting all its variations. Each falls once until
    its stock is above its limit again."""
    item = await db.get_one(Item, item_id)
    if item.kind is ItemKind.VARIATION:
        variation = await db.get_one(Variation, item_id)
        product = await db.get_one(Product, variation.product_id)
        variations = select(Variation.id).where(Variation.product_id == product.id)
        held = (
            await db.exec(
                select(func.coalesce(func.sum(StockLevel.quantity), 0)).where(
                    col(StockLevel.item_id).in_(variations)
                )
            )
        ).one()
        limit = product.low_stock_limit if product.status is ProductStatus.ACTIVE else None
        kind, record_id, name, unit = "product", product.id, product.name, None
        was, flag = product.low_stock, update(Product).where(col(Product.id) == product.id)
    else:
        stocked = await db.get_one(Material if item.kind is ItemKind.MATERIAL else Base, item_id)
        held = await on_hand(db, item_id)
        limit = stocked.low_stock_limit
        if isinstance(stocked, Material) and not stocked.inventoriable:
            limit = None
        kind, record_id, name = item.kind.value, item_id, stocked.name
        unit = (await db.get_one(Unit, stocked.tracking_unit_id)).name
        was, flag = item.low_stock, update(Item).where(col(Item.id) == item_id)
    low = limit is not None and Decimal(held) <= limit
    if low == was:
        return
    # A statement, not a change to the record, so nobody is recorded as changing it.
    await db.execute(flag.values(low_stock=low))
    if low:
        payload = {
            "kind": kind,
            "id": record_id,
            "name": name,
            "unit": unit,
            "on_hand": str(Decimal(held).quantize(PLACES)),
            "low_stock_limit": str(limit.quantize(PLACES)),
        }
        await record(db, Event(EventName.STOCK_LOW, payload))
