"""Hoard's stock: its items (materials, bases and variations), where they sit,
and changes made by hand.

Whatever changes stock takes a :class:`~seneschal_core.db.WriteSession`, which
only a community that may change opens. Each change is an adjustment; the
database keeps stock levels and lot balances from them, and every service that
changes an item's adjustments re-derives its cost (``hoard.costing``) before it
returns. A change made outside these services, such as editing or deleting an
adjustment, is followed by :func:`hoard.costing.rederive` for its item.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import costing
from hoard.holds import held_lots
from hoard.records import (
    Adjustment,
    AdjustmentReason,
    Base,
    Category,
    DefaultHoldReason,
    DefaultReason,
    HoldReason,
    Item,
    ItemKind,
    Location,
    Lot,
    Material,
    Picking,
    Product,
    StockLevel,
    Variation,
)
from seneschal_core.db import WriteSession


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
    """Give the organization the :class:`DefaultReason` reasons, keeping any it has."""
    await db.execute(
        insert(AdjustmentReason)
        .values([{"organization_id": db.organization_id, "key": key} for key in DefaultReason])
        .on_conflict_do_nothing()
    )


async def add_default_hold_reasons(db: WriteSession) -> None:
    """Give the organization the :class:`DefaultHoldReason` reasons, keeping any it has."""
    await db.execute(
        insert(HoldReason)
        .values([{"organization_id": db.organization_id, "key": key} for key in DefaultHoldReason])
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


async def add_base(db: WriteSession, base: Base) -> Base:
    """Add ``base``, as a new item."""
    base.id = await _item(db, ItemKind.BASE)
    base.organization_id = db.organization_id
    db.add(base)
    await db.flush()
    return base


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
    """Remove a material, base or variation with its lots, stock and adjustments.
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


async def picking(db: AsyncSession, item_id: int) -> Picking:
    """Which lot of an item goes first: its category's rule, or, for an item
    with no category, the earliest expiry first. A variation's category is
    its product's."""
    category_id = (
        await db.exec(
            select(func.coalesce(Material.category_id, Base.category_id, Product.category_id))
            .select_from(Item)
            .outerjoin(Material, col(Material.id) == Item.id)
            .outerjoin(Base, col(Base.id) == Item.id)
            .outerjoin(Variation, col(Variation.id) == Item.id)
            .outerjoin(Product, col(Product.id) == Variation.product_id)
            .where(Item.id == item_id)
        )
    ).first()
    category = await db.get(Category, category_id) if category_id is not None else None
    return category.picking if category is not None else Picking.EARLIEST_EXPIRY


#: The order each picking rule takes an item's lots in.
_ORDER = {
    Picking.EARLIEST_EXPIRY: (
        col(Lot.expires_on).asc().nulls_last(),
        col(Lot.dated_on),
        col(Lot.id),
    ),
    Picking.FIRST_IN: (col(Lot.dated_on), col(Lot.id)),
    Picking.LAST_IN: (col(Lot.dated_on).desc(), col(Lot.id).desc()),
}


async def draw_lots(
    db: AsyncSession,
    item_id: int,
    location_id: int,
    quantity: Decimal,
    drawn: MutableMapping[int, Decimal],
) -> list[tuple[int | None, Decimal]]:
    """The lots ``quantity`` of an item at a location is assumed to come from,
    and how much from each: the lots holding stock there that are not on
    hold, in the order the item's picking rule (:func:`picking`) takes them,
    each no further than its balance less what ``drawn`` already takes from
    it, which this adds to. Whatever the lots do not hold draws on no lot
    (``None``), and so does all of it where the rule assumes no lot."""
    rule = await picking(db, item_id)
    if rule is Picking.RECORDED:
        return [(None, quantity)]
    lots = await db.exec(
        select(Lot)
        .where(
            Lot.item_id == item_id,
            Lot.location_id == location_id,
            col(Lot.id).not_in(held_lots()),
        )
        .order_by(*_ORDER[rule])
        .execution_options(populate_existing=True)
    )
    draws: list[tuple[int | None, Decimal]] = []
    need = quantity
    for lot in lots.all():
        take = min(need, lot.balance - drawn.get(lot.id, Decimal(0)))
        if take <= 0:
            continue
        draws.append((lot.id, take))
        drawn[lot.id] = drawn.get(lot.id, Decimal(0)) + take
        need -= take
        if need == 0:
            break
    if need > 0:
        draws.append((None, need))
    return draws
