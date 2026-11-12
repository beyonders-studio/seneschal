"""Orders: goods sold, by hand to a customer among the community's contacts or
copied in from a store, and shipped in one or more shipments
(``hoard.shipments``).

An open order **commits** what its lines have not yet shipped, at its
location: a pending, shipped or cancelled one commits nothing, and a bundle's
own line commits nothing, since its parts are lines of their own. What is
**available** is what is on hand less what is committed, which is what Hoard
tells each store it may sell.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import col, exists, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    Order,
    OrderLine,
    OrderStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
)
from hoard.stock import on_hand


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
