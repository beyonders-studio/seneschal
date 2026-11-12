"""Orders: goods sold, by hand to a customer among the community's contacts or
copied in from a store, and shipped in one or more shipments
(``hoard.shipments``).

An open order **commits** what its lines have not yet shipped, at its
location: a pending, shipped or cancelled one commits nothing, and a bundle's
own line commits nothing, since its parts are lines of their own. What is
**available** is what is on hand less what is committed, which is what Hoard
tells each store it may sell.

An order is made by hand, with its lines, or copied in from a store
(``hoard.sync``), whose lines change only there. A bundle sold by hand is
split into its parts, as its recipe holds them, each a line of its own at no
price. An order is **shipped** once its shipments have shipped every line, and
open again once one is unshipped; cancelling it cancels the shipments still
pending, and it commits nothing until it is reopened. Whatever changes what an
order commits notes it for the items' listings (``hoard.listings``).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, update
from sqlalchemy.orm import aliased
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import recipes
from hoard.records import (
    Order,
    OrderLine,
    OrderStatus,
    Product,
    Recipe,
    RecipeLine,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    Variation,
)
from hoard.stock import on_hand
from seneschal_core.db import WriteSession


class OrderError(StrEnum):
    """Why a change to an order was refused: codes pages translate."""

    #: What was sold is not a product's variation.
    ORDER_ITEM_NOT_SOLD = "ORDER_ITEM_NOT_SOLD"
    #: The order is cancelled: it changes once it is reopened.
    ORDER_CANCELLED = "ORDER_CANCELLED"
    #: The order is not cancelled, so there is nothing to reopen.
    ORDER_NOT_CANCELLED = "ORDER_NOT_CANCELLED"
    #: A store's order: its lines change in the store.
    ORDER_FROM_STORE = "ORDER_FROM_STORE"
    #: A bundle's part: it changes with its bundle's line.
    ORDER_LINE_PART = "ORDER_LINE_PART"
    #: A shipment holds the line, or more of it than asked for.
    ORDER_LINE_SHIPPED = "ORDER_LINE_SHIPPED"
    #: Something of the order has shipped, so it is kept.
    ORDER_SHIPPED = "ORDER_SHIPPED"


class OrderRefused(Exception):
    def __init__(self, code: OrderError) -> None:
        super().__init__(code.value)
        self.code = code


async def committed(db: AsyncSession, item_id: int, location_id: int | None = None) -> Decimal:
    """What open orders hold of an item and have not shipped: at one location,
    or at all of them."""
    shipped = (
        select(func.coalesce(func.sum(ShipmentLine.quantity), 0))
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .where(
            ShipmentLine.order_line_id == OrderLine.id,
            Shipment.status == ShipmentStatus.SHIPPED,
        )
        .correlate(OrderLine)
        .scalar_subquery()
    )
    part = aliased(OrderLine)
    query = (
        select(func.coalesce(func.sum(func.greatest(OrderLine.quantity - shipped, 0)), 0))
        .join(Order, col(Order.id) == OrderLine.order_id)
        .where(
            OrderLine.item_id == item_id,
            Order.status == OrderStatus.OPEN,
            ~exists().where(col(part.bundle_line_id) == OrderLine.id),
        )
    )
    if location_id is not None:
        query = query.where(Order.location_id == location_id)
    return Decimal((await db.exec(query)).one())


async def available(db: AsyncSession, item_id: int, location_id: int | None = None) -> Decimal:
    """What is on hand of an item less what open orders commit: at one
    location, or at all of them."""
    return await on_hand(db, item_id, location_id) - await committed(db, item_id, location_id)


async def lines(db: AsyncSession, order_id: int) -> list[OrderLine]:
    """An order's lines, in the order they were added, each bundle's parts
    after it."""
    found = await db.exec(
        select(OrderLine)
        .where(OrderLine.order_id == order_id)
        .order_by(col(OrderLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


def shippable(order_lines: list[OrderLine]) -> list[OrderLine]:
    """The lines that ship themselves: all but a bundle's own line, whose
    parts ship instead."""
    bundles = {line.bundle_line_id for line in order_lines if line.bundle_line_id}
    return [line for line in order_lines if line.id not in bundles]


async def held(
    db: AsyncSession, order_id: int, *, shipped_only: bool = False
) -> dict[int, Decimal]:
    """How much of each of an order's lines its shipments hold, pending or
    shipped, or only shipped, by line id."""
    statuses = [ShipmentStatus.SHIPPED]
    if not shipped_only:
        statuses.append(ShipmentStatus.PENDING)
    found = await db.exec(
        select(ShipmentLine.order_line_id, func.sum(ShipmentLine.quantity))
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .where(Shipment.order_id == order_id, col(Shipment.status).in_(statuses))
        .group_by(col(ShipmentLine.order_line_id))
    )
    return defaultdict(Decimal, {line_id: Decimal(quantity) for line_id, quantity in found.all()})


async def _note(db: WriteSession, item_ids: set[int]) -> None:
    # Imported here: listings read what is available through this module.
    from hoard import listings

    for item_id in sorted(item_ids):
        await listings.note_changed(db, item_id)


async def _items(db: WriteSession, order: Order) -> set[int]:
    return {line.item_id for line in await lines(db, order.id)}


async def note(db: WriteSession, order: Order) -> None:
    """Note that what ``order`` commits may have changed, such as after it
    moves to another location, for its items' listings."""
    await _note(db, await _items(db, order))


def default_price(product: Product, variation: Variation) -> Decimal:
    """What a variation sells for unless an order says otherwise."""
    return variation.price if variation.price is not None else product.default_price or Decimal(0)


async def _variation(db: WriteSession, item_id: int) -> tuple[Product, Variation]:
    variation = await db.get(Variation, item_id)
    if variation is None:
        raise OrderRefused(OrderError.ORDER_ITEM_NOT_SOLD)
    return await db.get_one(Product, variation.product_id), variation


def _by_hand(order: Order) -> None:
    if order.store_id is not None:
        raise OrderRefused(OrderError.ORDER_FROM_STORE)
    if order.status is OrderStatus.CANCELLED:
        raise OrderRefused(OrderError.ORDER_CANCELLED)


async def _parts(db: WriteSession, product: Product) -> list[tuple[RecipeLine, Decimal]]:
    """A bundle's parts as its recipe holds them, each with what one bundle holds."""
    recipe = (await db.exec(select(Recipe).where(Recipe.product_id == product.id))).first()
    if recipe is None:
        return []
    held_lines = (await recipes.lines_of(db, [recipe.id])).get(recipe.id, [])
    return [(line, line.quantity / recipe.batch_quantity) for line in held_lines]


async def _split(db: WriteSession, line: OrderLine, product: Product) -> None:
    """Make ``line``'s parts again, when it sells a bundle."""
    for part in await db.exec(select(OrderLine).where(OrderLine.bundle_line_id == line.id)):
        await db.delete(part)
    await db.flush()
    if not product.bundle:
        return
    for recipe_line, each in await _parts(db, product):
        db.add(
            OrderLine(
                organization_id=db.organization_id,
                order_id=line.order_id,
                item_id=recipe_line.item_id,
                quantity=recipes.scaled(line.quantity, each),
                price=Decimal(0),
                bundle_line_id=line.id,
            )
        )
    await db.flush()


async def add_line(
    db: WriteSession, order: Order, item_id: int, quantity: Decimal, price: Decimal | None = None
) -> OrderLine:
    """Sell ``quantity`` of a variation on a hand order, at ``price`` each or
    else its own; a bundle is split into its parts."""
    _by_hand(order)
    product, variation = await _variation(db, item_id)
    line = OrderLine(
        organization_id=db.organization_id,
        order_id=order.id,
        item_id=variation.id,
        quantity=quantity,
        price=default_price(product, variation) if price is None else price,
    )
    db.add(line)
    await db.flush()
    await _split(db, line, product)
    await settle(db, order)
    await _note(db, await _items(db, order))
    return line


async def change_line(
    db: WriteSession,
    order: Order,
    line: OrderLine,
    *,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
) -> None:
    """Change a hand order's line: no less than its shipments hold of it, and
    a bundle's only while none of its parts is in a shipment."""
    _by_hand(order)
    if line.bundle_line_id is not None:
        raise OrderRefused(OrderError.ORDER_LINE_PART)
    if price is not None:
        line.price = price
    if quantity is not None and quantity != line.quantity:
        holding = await held(db, order.id)
        parts = [part for part in await lines(db, order.id) if part.bundle_line_id == line.id]
        if quantity < holding[line.id] or any(holding[part.id] for part in parts):
            raise OrderRefused(OrderError.ORDER_LINE_SHIPPED)
        before = await _items(db, order)
        line.quantity = quantity
        await db.flush()
        if parts:
            product, _ = await _variation(db, line.item_id)
            await _split(db, line, product)
        await settle(db, order)
        await _note(db, before | await _items(db, order))
    await db.flush()


async def remove_line(db: WriteSession, order: Order, line: OrderLine) -> None:
    """Remove a hand order's line, with a bundle's parts, while no shipment
    holds any of them."""
    _by_hand(order)
    if line.bundle_line_id is not None:
        raise OrderRefused(OrderError.ORDER_LINE_PART)
    holding = await held(db, order.id)
    removed = [one for one in await lines(db, order.id) if line.id in (one.id, one.bundle_line_id)]
    if any(holding[one.id] for one in removed):
        raise OrderRefused(OrderError.ORDER_LINE_SHIPPED)
    await db.delete(line)
    await db.flush()
    await settle(db, order)
    await _note(db, {one.item_id for one in removed})


async def settle(db: WriteSession, order: Order) -> None:
    """Mark an open order shipped once its shipments have shipped every line
    it ships, and a shipped one open again once they have not."""
    if order.status not in (OrderStatus.OPEN, OrderStatus.SHIPPED):
        return
    shipped = await held(db, order.id, shipped_only=True)
    to_ship = shippable(await lines(db, order.id))
    done = bool(to_ship) and all(shipped[line.id] >= line.quantity for line in to_ship)
    order.status = OrderStatus.SHIPPED if done else OrderStatus.OPEN
    await db.flush()


async def cancel(db: WriteSession, order: Order) -> None:
    """Cancel an order: its pending shipments are cancelled, what has shipped
    stays shipped, and it commits nothing more."""
    if order.status is OrderStatus.CANCELLED:
        raise OrderRefused(OrderError.ORDER_CANCELLED)
    await db.execute(
        update(Shipment)
        .where(col(Shipment.order_id) == order.id, col(Shipment.status) == ShipmentStatus.PENDING)
        .values(status=ShipmentStatus.CANCELLED)
    )
    order.status = OrderStatus.CANCELLED
    await db.flush()
    await _note(db, await _items(db, order))


async def reopen(db: WriteSession, order: Order) -> None:
    """Reopen a cancelled order: open, or pending while a store's order is
    unpaid, or shipped when nothing is left to ship. Its cancelled shipments
    stay cancelled."""
    if order.status is not OrderStatus.CANCELLED:
        raise OrderRefused(OrderError.ORDER_NOT_CANCELLED)
    unpaid = order.store_id is not None and not order.paid
    order.status = OrderStatus.PENDING if unpaid else OrderStatus.OPEN
    await settle(db, order)
    await _note(db, await _items(db, order))


async def delete(db: WriteSession, order: Order) -> None:
    """Delete a hand order with its shipments, while none of them has shipped."""
    if order.store_id is not None:
        raise OrderRefused(OrderError.ORDER_FROM_STORE)
    shipped = await db.exec(
        select(func.count())
        .select_from(Shipment)
        .where(Shipment.order_id == order.id, Shipment.status == ShipmentStatus.SHIPPED)
    )
    if shipped.one():
        raise OrderRefused(OrderError.ORDER_SHIPPED)
    items = await _items(db, order)
    await db.delete(order)
    await db.flush()
    await _note(db, items)
