"""Orders: by hand for a customer among the community's contacts, or copied
in from a store, with their lines and what each commits (``hoard.orders``).

An order is read whole: its lines, how much of each its shipments hold and
have shipped, and its shipments, each with its lines, their lots and what came
back of them (``hoard.api.shipments`` changes those). A hand order names its
customer by an Initiative contact's id; a store's order names the store and its
number there instead, and its lines change only in the store.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from hoard import orders
from hoard.api.adjustments import (
    NOT_FOUND,
    AdjustmentItem,
    NonNegative,
    Responses,
    changes,
    describe_items,
    known,
    location_or_main,
)
from hoard.api.deps import HoardError
from hoard.api.shapes import Page, PageDep
from hoard.orders import OrderError, OrderRefused
from hoard.records import (
    PLACES,
    Lot,
    Order,
    OrderLine,
    OrderStatus,
    ReturnLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)
from seneschal_core.api import OrganizationSessionDep, SessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession, WriteSession

router = APIRouter(prefix="/orders", tags=["orders"])

#: A quantity sold or shipped: more than none.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
#: An address, a tracking number or a reference, typed in.
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
CHANGE_REFUSED: Responses = {
    409: {
        "description": "ORDER_CANCELLED, ORDER_NOT_CANCELLED, ORDER_FROM_STORE, "
        "ORDER_LINE_PART, ORDER_LINE_SHIPPED, ORDER_SHIPPED"
    },
    422: {"description": "ORDER_ITEM_NOT_SOLD"},
}


class OrderCodeError(StrEnum):
    """Why an order's code was refused."""

    #: Another order has the code.
    ORDER_CODE_TAKEN = "ORDER_CODE_TAKEN"


class OrderLot(BaseModel):
    """A lot a shipment line draws, or may."""

    id: int
    number: str
    expires_on: date | None
    #: What is left of it.
    balance: Decimal


class OrderReturnRead(BaseModel):
    """What came back of a shipment line."""

    id: int
    quantity: Decimal
    dated_on: date
    #: Written off as returned damaged rather than restocked.
    damaged: bool


class OrderShipmentLineRead(BaseModel):
    id: int
    order_line_id: int
    item: AdjustmentItem
    quantity: Decimal
    #: The lot it draws: recorded, or assumed when it shipped.
    lot: OrderLot | None
    assumed: bool
    #: While the shipment is pending, the lots of its item at the shipment's
    #: location that hold stock, the earliest expiry first.
    lots: list[OrderLot]
    returns: list[OrderReturnRead]


class OrderShipmentRead(BaseModel):
    id: int
    location_id: int
    status: ShipmentStatus
    shipped_on: date | None
    #: The store's id for the fulfillment it records.
    external_id: str | None
    #: The carrier: an Initiative contact's id.
    carrier_contact_id: int | None
    tracking: str | None
    delivery_address: str | None
    reference: str | None
    lines: list[OrderShipmentLineRead]


class OrderLineRead(BaseModel):
    id: int
    item: AdjustmentItem
    quantity: Decimal
    #: What one sold for.
    price: Decimal
    #: The bundle's line, for one of its parts.
    bundle_line_id: int | None
    #: How much of it its pending and shipped shipments hold.
    held: Decimal
    #: How much of it has shipped.
    shipped: Decimal
    production_run_id: int | None
    selling_plan: str | None


class OrderSummary(BaseModel):
    id: int
    code: str
    #: The store it was copied from, and its number there.
    store_id: int | None
    external_number: str | None
    #: The customer: an Initiative contact's id.
    customer_contact_id: int | None
    location_id: int
    ordered_on: date
    due_on: date | None
    paid: bool
    status: OrderStatus
    #: Its lines, less discount, with shipping and tax.
    total: Decimal


class OrderRead(OrderSummary):
    external_id: str | None
    initiative_id: int | None
    shipping: Decimal
    tax: Decimal
    discount: Decimal
    delivery_address: str | None
    lines: list[OrderLineRead]
    shipments: list[OrderShipmentRead]
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


def _summary(order: Order, value: Decimal) -> dict[str, Any]:
    return {
        "id": order.id,
        "code": order.code,
        "store_id": order.store_id,
        "external_number": order.external_number,
        "customer_contact_id": order.customer_contact_id,
        "location_id": order.location_id,
        "ordered_on": order.ordered_on,
        "due_on": order.due_on,
        "paid": order.paid,
        "status": order.status,
        "total": (value - order.discount + order.shipping + order.tax).quantize(PLACES),
    }


def _lot(lot: Lot) -> OrderLot:
    return OrderLot(id=lot.id, number=lot.number, expires_on=lot.expires_on, balance=lot.balance)


async def _shipments(
    db: OrganizationSession, order_id: int
) -> tuple[list[Shipment], list[ShipmentLine]]:
    found = await db.exec(
        select(Shipment)
        .where(Shipment.order_id == order_id)
        .order_by(col(Shipment.id))
        .execution_options(populate_existing=True)
    )
    sent = list(found.all())
    held = await db.exec(
        select(ShipmentLine)
        .where(col(ShipmentLine.shipment_id).in_([shipment.id for shipment in sent]))
        .order_by(col(ShipmentLine.id))
        .execution_options(populate_existing=True)
    )
    return sent, list(held.all())


async def _choices(
    db: OrganizationSession, sent: Sequence[Shipment], held: Sequence[ShipmentLine]
) -> dict[tuple[int, int], list[OrderLot]]:
    """The lots holding stock that each pending shipment's lines may draw, by
    item and location."""
    pending = {s.id: s.location_id for s in sent if s.status is ShipmentStatus.PENDING}
    wanted = {
        (line.item_id, pending[line.shipment_id]) for line in held if line.shipment_id in pending
    }
    if not wanted:
        return {}
    found = await db.exec(
        select(Lot)
        .where(
            col(Lot.item_id).in_({item_id for item_id, _ in wanted}),
            col(Lot.location_id).in_({location_id for _, location_id in wanted}),
            col(Lot.balance) > 0,
        )
        .order_by(col(Lot.expires_on).asc().nulls_last(), col(Lot.dated_on), col(Lot.id))
    )
    choices: dict[tuple[int, int], list[OrderLot]] = {}
    for lot in found.all():
        choices.setdefault((lot.item_id, lot.location_id), []).append(_lot(lot))
    return choices


async def _returns(
    db: OrganizationSession, held: Sequence[ShipmentLine]
) -> dict[int, list[OrderReturnRead]]:
    found = await db.exec(
        select(ReturnLine)
        .where(col(ReturnLine.shipment_line_id).in_([line.id for line in held]))
        .order_by(col(ReturnLine.dated_on), col(ReturnLine.id))
    )
    back: dict[int, list[OrderReturnRead]] = {}
    for returned in found.all():
        back.setdefault(returned.shipment_line_id, []).append(
            OrderReturnRead(
                id=returned.id,
                quantity=returned.quantity,
                dated_on=returned.dated_on,
                damaged=returned.damaged,
            )
        )
    return back


async def describe_order(db: OrganizationSession, order: Order) -> OrderRead:
    """``order`` as the database holds it, with its lines and shipments."""
    await db.refresh(order)
    order_lines = await orders.lines(db, order.id)
    holding = await orders.held(db, order.id)
    shipped = await orders.held(db, order.id, shipped_only=True)
    sent, held = await _shipments(db, order.id)
    items = await describe_items(db, [line.item_id for line in order_lines])
    found = await db.exec(
        select(Lot)
        .where(col(Lot.id).in_({line.lot_id for line in held if line.lot_id}))
        .execution_options(populate_existing=True)
    )
    lots = {lot.id: lot for lot in found.all()}
    back = await _returns(db, held)
    choices = await _choices(db, sent, held)
    value = sum((line.quantity * line.price for line in order_lines), Decimal(0))
    return OrderRead(
        **_summary(order, value),
        external_id=order.external_id,
        initiative_id=order.initiative_id,
        shipping=order.shipping,
        tax=order.tax,
        discount=order.discount,
        delivery_address=order.delivery_address,
        lines=[
            OrderLineRead(
                id=line.id,
                item=items[line.item_id],
                quantity=line.quantity,
                price=line.price,
                bundle_line_id=line.bundle_line_id,
                held=holding[line.id],
                shipped=shipped[line.id],
                production_run_id=line.production_run_id,
                selling_plan=line.selling_plan,
            )
            for line in order_lines
        ],
        shipments=[
            OrderShipmentRead(
                id=shipment.id,
                location_id=shipment.location_id,
                status=shipment.status,
                shipped_on=shipment.shipped_on,
                external_id=shipment.external_id,
                carrier_contact_id=shipment.carrier_contact_id,
                tracking=shipment.tracking,
                delivery_address=shipment.delivery_address,
                reference=shipment.reference,
                lines=[
                    OrderShipmentLineRead(
                        id=line.id,
                        order_line_id=line.order_line_id,
                        item=items[line.item_id],
                        quantity=line.quantity,
                        lot=_lot(lots[line.lot_id]) if line.lot_id in lots else None,
                        assumed=line.assumed,
                        lots=choices.get((line.item_id, shipment.location_id), [])
                        if shipment.status is ShipmentStatus.PENDING
                        else [],
                        returns=back.get(line.id, []),
                    )
                    for line in held
                    if line.shipment_id == shipment.id
                ],
            )
            for shipment in sent
        ],
        created_at=order.created_at,
        created_by=order.created_by,
        updated_at=order.updated_at,
        updated_by=order.updated_by,
    )


def order_refused(refused: OrderRefused) -> Exception:
    """The answer to a change :mod:`hoard.orders` refused."""
    if refused.code is OrderError.ORDER_ITEM_NOT_SOLD:
        return refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code)
    return refuse(status.HTTP_409_CONFLICT, refused.code)


async def _flush_code(db: WriteSession) -> None:
    """Flush an order, refusing a code another order has."""
    try:
        await db.flush()
    except IntegrityError as error:
        if "uq_orders_code" not in str(error.orig):
            raise
        raise refuse(status.HTTP_409_CONFLICT, OrderCodeError.ORDER_CODE_TAKEN) from None


@router.get("")
async def list_orders(
    db: OrganizationSessionDep,
    page: PageDep,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
) -> Page[OrderSummary]:
    """Orders, the latest first: of one status, or all."""
    value = (
        select(OrderLine.order_id, func.sum(OrderLine.quantity * OrderLine.price).label("value"))
        .group_by(col(OrderLine.order_id))
        .subquery()
    )
    counted = select(func.count()).select_from(Order)
    query = select(Order, value.c.value).outerjoin(value, value.c.order_id == col(Order.id))
    if order_status is not None:
        counted = counted.where(Order.status == order_status)
        query = query.where(Order.status == order_status)
    total = (await db.exec(counted)).one()
    rows = await db.exec(
        query.order_by(col(Order.ordered_on).desc(), col(Order.id).desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    return Page(
        items=[OrderSummary(**_summary(order, value or Decimal(0))) for order, value in rows.all()],
        total=total,
    )


@router.get("/{order_id}", responses=NOT_FOUND)
async def read_order(db: OrganizationSessionDep, order_id: int) -> OrderRead:
    """An order, its lines, and its shipments with their lots and returns."""
    return await describe_order(db, await known(db, Order, order_id))


class OrderLineCreate(BaseModel):
    #: The variation sold.
    item_id: int
    quantity: Positive
    #: What one sells for; the variation's price, or its product's, when not given.
    price: NonNegative | None = None


class OrderCreate(BaseModel):
    #: The next free ``O-`` number when not given.
    code: Code | None = None
    #: Today when not given.
    ordered_on: date | None = None
    due_on: date | None = None
    #: The customer: an Initiative contact's id.
    customer_contact_id: int
    #: The main location when not given.
    location_id: int | None = None
    paid: bool = False
    shipping: NonNegative | None = None
    tax: NonNegative | None = None
    discount: NonNegative | None = None
    delivery_address: Text | None = None
    lines: list[OrderLineCreate] = Field(default_factory=list, max_length=500)


async def _next_code(db: WriteSession) -> str:
    latest = (await db.exec(select(func.max(Order.id)))).one()
    return f"O-{(latest or 0) + 1:04d}"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        **NOT_FOUND,
        409: {"description": "ORDER_CODE_TAKEN"},
        422: {"description": "ORDER_ITEM_NOT_SOLD"},
    },
)
async def create_order(db: WriteSessionDep, session: SessionDep, body: OrderCreate) -> OrderRead:
    """Add an order by hand, open, with its lines, in the initiative the page
    was opened in."""
    order = Order(
        organization_id=db.organization_id,
        code=body.code or await _next_code(db),
        ordered_on=body.ordered_on or date.today(),
        due_on=body.due_on,
        customer_contact_id=body.customer_contact_id,
        location_id=await location_or_main(db, body.location_id),
        initiative_id=session.initiative_id,
        paid=body.paid,
        shipping=body.shipping or Decimal(0),
        tax=body.tax or Decimal(0),
        discount=body.discount or Decimal(0),
        delivery_address=body.delivery_address or None,
    )
    db.add(order)
    await _flush_code(db)
    try:
        for line in body.lines:
            await orders.add_line(db, order, line.item_id, line.quantity, line.price)
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


class OrderUpdate(BaseModel):
    """A change to an order: only what is sent."""

    code: Code | None = None
    ordered_on: date | None = None
    #: Null takes the due date off.
    due_on: date | None = None
    customer_contact_id: int | None = None
    location_id: int | None = None
    paid: bool | None = None
    shipping: NonNegative | None = None
    tax: NonNegative | None = None
    discount: NonNegative | None = None
    #: Null takes the address off.
    delivery_address: Text | None = None


@router.patch("/{order_id}", responses={**NOT_FOUND, 409: {"description": "ORDER_CODE_TAKEN"}})
async def update_order(db: WriteSessionDep, order_id: int, body: OrderUpdate) -> OrderRead:
    """Change an order."""
    sent = changes(body, nullable={"due_on", "delivery_address"})
    order = await known(db, Order, order_id)
    if "location_id" in sent:
        sent["location_id"] = await location_or_main(db, sent["location_id"])
    if "delivery_address" in sent:
        sent["delivery_address"] = sent["delivery_address"] or None
    for name, value in sent.items():
        setattr(order, name, value)
    await _flush_code(db)
    if "location_id" in sent:
        await orders.note(db, order)
    await db.commit()
    return await describe_order(db, order)


@router.delete(
    "/{order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**NOT_FOUND, **CHANGE_REFUSED},
)
async def delete_order(db: WriteSessionDep, order_id: int) -> None:
    """Delete a hand order with its shipments, while none of them has shipped."""
    try:
        await orders.delete(db, await known(db, Order, order_id))
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()


@router.post("/{order_id}/cancel", responses={**NOT_FOUND, **CHANGE_REFUSED})
async def cancel_order(db: WriteSessionDep, order_id: int) -> OrderRead:
    """Cancel an order and its pending shipments; what shipped stays shipped."""
    order = await known(db, Order, order_id)
    try:
        await orders.cancel(db, order)
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


@router.post("/{order_id}/reopen", responses={**NOT_FOUND, **CHANGE_REFUSED})
async def reopen_order(db: WriteSessionDep, order_id: int) -> OrderRead:
    """Reopen a cancelled order."""
    order = await known(db, Order, order_id)
    try:
        await orders.reopen(db, order)
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


async def _line(db: WriteSession, order_id: int, line_id: int) -> tuple[Order, OrderLine]:
    order = await known(db, Order, order_id)
    line = await known(db, OrderLine, line_id)
    if line.order_id != order.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return order, line


@router.post(
    "/{order_id}/lines",
    status_code=status.HTTP_201_CREATED,
    responses={**NOT_FOUND, **CHANGE_REFUSED},
)
async def create_order_line(db: WriteSessionDep, order_id: int, body: OrderLineCreate) -> OrderRead:
    """Sell a variation on a hand order; a bundle comes with its parts."""
    order = await known(db, Order, order_id)
    try:
        await orders.add_line(db, order, body.item_id, body.quantity, body.price)
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


class OrderLineUpdate(BaseModel):
    """A change to a line: only what is sent."""

    quantity: Positive | None = None
    price: NonNegative | None = None


@router.patch("/{order_id}/lines/{line_id}", responses={**NOT_FOUND, **CHANGE_REFUSED})
async def update_order_line(
    db: WriteSessionDep, order_id: int, line_id: int, body: OrderLineUpdate
) -> OrderRead:
    """Change a hand order's line: no less than its shipments hold of it."""
    sent = changes(body)
    order, line = await _line(db, order_id, line_id)
    try:
        await orders.change_line(
            db, order, line, quantity=sent.get("quantity"), price=sent.get("price")
        )
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)


@router.delete("/{order_id}/lines/{line_id}", responses={**NOT_FOUND, **CHANGE_REFUSED})
async def delete_order_line(db: WriteSessionDep, order_id: int, line_id: int) -> OrderRead:
    """Remove a hand order's line, with a bundle's parts, while no shipment holds them."""
    order, line = await _line(db, order_id, line_id)
    try:
        await orders.remove_line(db, order, line)
    except OrderRefused as refused:
        raise order_refused(refused) from None
    await db.commit()
    return await describe_order(db, order)
