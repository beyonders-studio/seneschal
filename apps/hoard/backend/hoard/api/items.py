"""What the pages of materials, bases and products share: the fields they
take, who made a record and changed it, and an item's stock and lots at each
location."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.api.properties import HoardPropertySummary, values_of
from hoard.records import Lot, RecordKind, StockLevel

#: A name or code someone types.
Text = Annotated[str, Field(min_length=1, max_length=200)]
OptionalText = Text | None
#: A quantity, cost or price: exact, to six places, and never below zero.
Amount = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
#: The same, above zero.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]


class ItemStamps(BaseModel):
    """Who made a record and when, and who last changed it and when: a
    member's reference, or null for Hoard itself."""

    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


class ItemStockRead(BaseModel):
    """What is on hand of an item at one location."""

    location_id: int
    quantity: Decimal


class ItemLotRead(BaseModel):
    """A lot of an item at a location, and what is left of it."""

    id: int
    location_id: int
    number: str
    dated_on: date
    expires_on: date | None
    notes: str | None
    balance: Decimal
    #: What it holds of the properties people add to lots.
    properties: list[HoardPropertySummary]


class ItemHoldings(BaseModel):
    """An item's stock at each location it has been at, and its lots."""

    on_hand: Decimal
    stock: list[ItemStockRead]
    lots: list[ItemLotRead]


async def on_hand(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, Decimal]:
    """What is on hand of each item, at all its locations."""
    totals = await db.exec(
        select(StockLevel.item_id, func.sum(StockLevel.quantity))
        .where(col(StockLevel.item_id).in_(list(item_ids)))
        .group_by(col(StockLevel.item_id))
    )
    return {item_id: Decimal(total) for item_id, total in totals}


async def holdings(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, ItemHoldings]:
    """Each item's stock per location and its lots, oldest first."""
    ids = list(item_ids)
    held = {item_id: ItemHoldings(on_hand=Decimal(0), stock=[], lots=[]) for item_id in ids}
    levels = await db.exec(
        select(StockLevel)
        .where(col(StockLevel.item_id).in_(ids))
        .order_by(col(StockLevel.location_id))
    )
    for level in levels:
        item = held[level.item_id]
        item.stock.append(ItemStockRead(location_id=level.location_id, quantity=level.quantity))
        item.on_hand += level.quantity
    lots = (
        await db.exec(
            select(Lot).where(col(Lot.item_id).in_(ids)).order_by(col(Lot.dated_on), col(Lot.id))
        )
    ).all()
    values = await values_of(db, RecordKind.LOT, [lot.id for lot in lots])
    for lot in lots:
        held[lot.item_id].lots.append(
            ItemLotRead.model_validate({**lot.model_dump(), "properties": values[lot.id]})
        )
    return held
