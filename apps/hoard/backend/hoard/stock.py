"""Hoard's stock: its items, where they sit, and changes made by hand.

Whatever changes stock takes a :class:`~seneschal_core.db.WriteSession`, which
only a community that may change opens. Each change is an adjustment; the
database keeps stock levels and lot balances from them, and every service that
changes an item's adjustments re-derives its cost (``hoard.costing``) before it
returns. A change made outside these services, such as editing or deleting an
adjustment, is followed by :func:`hoard.costing.rederive` for its item.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import costing
from hoard.records import (
    Adjustment,
    AdjustmentReason,
    Item,
    ItemKind,
    Location,
    Material,
    Product,
    StockLevel,
    Variation,
)
from seneschal_core.db import WriteSession

#: The reasons an organization starts with, which people rename or remove.
DEFAULT_REASONS = ("Correction", "Damaged", "Expired", "Lost", "Personal use")


class StockError(StrEnum):
    """Why a change to stock was refused: codes pages translate."""

    NOT_INVENTORIABLE = "NOT_INVENTORIABLE"
    LOT_IN_USE = "LOT_IN_USE"


class StockRefused(Exception):
    def __init__(self, code: StockError) -> None:
        super().__init__(code.value)
        self.code = code


async def main_location(db: WriteSession) -> Location:
    """The organization's main location, made the first time it is asked for."""
    await db.execute(
        insert(Location)
        .values(organization_id=db.organization_id, main=True)
        .on_conflict_do_nothing(index_elements=["organization_id"], index_where=text("main"))
    )
    return (await db.exec(select(Location).where(Location.main))).one()


async def add_default_reasons(db: WriteSession) -> None:
    """Give the organization :data:`DEFAULT_REASONS`, keeping any it has."""
    await db.execute(
        insert(AdjustmentReason)
        .values([{"organization_id": db.organization_id, "name": name} for name in DEFAULT_REASONS])
        .on_conflict_do_nothing()
    )


async def _item(db: WriteSession, kind: ItemKind) -> int:
    item = Item(organization_id=db.organization_id, kind=kind)
    db.add(item)
    await db.flush()
    return item.id


async def add_material(db: WriteSession, material: Material) -> Material:
    """Add ``material``, as a new item."""
    material.id = await _item(db, ItemKind.MATERIAL)
    material.organization_id = db.organization_id
    db.add(material)
    await db.flush()
    return material


async def add_variation(db: WriteSession, variation: Variation) -> Variation:
    """Add ``variation`` to its product, as a new item."""
    variation.id = await _item(db, ItemKind.VARIATION)
    variation.organization_id = db.organization_id
    db.add(variation)
    await db.flush()
    return variation


async def add_product(db: WriteSession, product: Product) -> tuple[Product, Variation]:
    """Add ``product`` with its default variation, which a simple product sells."""
    product.organization_id = db.organization_id
    db.add(product)
    await db.flush()
    variation = await add_variation(db, Variation(product_id=product.id, is_default=True))
    return product, variation


async def remove_item(db: WriteSession, item_id: int) -> None:
    """Remove a material or variation with its lots, stock and adjustments.
    One a purchase bought is kept: removing it raises the database's
    ``IntegrityError``."""
    await db.execute(delete(Item).where(col(Item.id) == item_id))


async def remove_product(db: WriteSession, product: Product) -> None:
    """Remove ``product`` with its variations, as :func:`remove_item` does each."""
    variations = select(Variation.id).where(Variation.product_id == product.id)
    await db.execute(delete(Item).where(col(Item.id).in_(variations)))
    await db.delete(product)


async def adjust(db: WriteSession, adjustment: Adjustment) -> Adjustment:
    """Record ``adjustment``, a starting or manual change, and re-derive its
    item's cost. A material that is not inventoriable carries no stock."""
    material = await db.get(Material, adjustment.item_id)
    if material is not None and not material.inventoriable:
        raise StockRefused(StockError.NOT_INVENTORIABLE)
    adjustment.organization_id = db.organization_id
    db.add(adjustment)
    await costing.rederive(db, adjustment.item_id)
    return adjustment


async def on_hand(db: AsyncSession, item_id: int, location_id: int | None = None) -> Decimal:
    """What is on hand of an item: at one location, or at all of them."""
    query = select(func.coalesce(func.sum(StockLevel.quantity), 0)).where(
        StockLevel.item_id == item_id
    )
    if location_id is not None:
        query = query.where(StockLevel.location_id == location_id)
    return Decimal((await db.exec(query)).one())
