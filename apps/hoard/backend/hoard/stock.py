"""Hoard's stock: its items (materials, bases and variations), where they sit,
and changes made by hand.

Whatever changes stock takes a :class:`~seneschal_core.db.WriteSession`, which
only a community that may change opens. Each change is an adjustment; the
database keeps stock levels and lot balances from them, and every service that
changes an item's adjustments re-derives its cost (``hoard.costing``) before it
returns. A change made outside these services, such as editing or deleting an
adjustment, is followed by :func:`hoard.costing.rederive` for its item.

**A transfer** (:func:`transfer`) moves stock of an item from one location to
another as one record: an adjustment out of a lot, or of the stock there in no
lot, and one into the same lot at the other location, which is made the first
time, as a copy of it. A lot keeps going to and coming back from the same lot
at each location. A hold moves with its lot: the lot it moved into is held the
same way, and a lot moved whole takes its hold with it. A transfer is undone
by moving the stock back.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import costing
from hoard.holds import HoldError, held_lots, place
from hoard.records import (
    SHAPES,
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Base,
    Category,
    CategoryKind,
    DefaultHoldReason,
    DefaultReason,
    DocumentLink,
    Hold,
    HoldReason,
    Item,
    ItemKind,
    ItemType,
    Listing,
    Location,
    Lot,
    LotScan,
    Material,
    Picking,
    Product,
    PropertyValue,
    StockLevel,
    Transfer,
    Variation,
)
from seneschal_core.db import WriteSession


class StockError(StrEnum):
    """Why a change to stock was refused: codes pages translate."""

    NOT_INVENTORIABLE = "NOT_INVENTORIABLE"
    LOT_IN_USE = "LOT_IN_USE"
    #: A transfer goes to another location than the one it comes from.
    TRANSFER_SAME_LOCATION = "TRANSFER_SAME_LOCATION"
    #: The lot named is not of the item, or not at the location moved from.
    TRANSFER_LOT_ELSEWHERE = "TRANSFER_LOT_ELSEWHERE"
    #: More is moved than the lot holds, or than the location holds in no lot.
    TRANSFER_OVER_BALANCE = "TRANSFER_OVER_BALANCE"


class StockRefused(Exception):
    def __init__(self, code: StockError | HoldError) -> None:
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


async def add_default_types(db: WriteSession) -> None:
    """Give the organization each kind's default type, in its first shape,
    keeping any it has."""
    await db.execute(
        insert(ItemType)
        .values(
            [
                {
                    "organization_id": db.organization_id,
                    "kind": kind,
                    "shape": shapes[0],
                    "is_default": True,
                }
                for kind, shapes in SHAPES.items()
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["organization_id", "kind"], index_where=text("is_default")
        )
    )


async def default_type(db: WriteSession, kind: CategoryKind) -> int:
    """The id of ``kind``'s default type, made the first time it is asked for."""
    found = select(ItemType.id).where(ItemType.kind == kind, col(ItemType.is_default))
    type_id = (await db.exec(found)).first()
    if type_id is None:
        await add_default_types(db)
        type_id = (await db.exec(found)).one()
    return type_id


async def _item(db: WriteSession, kind: ItemKind) -> int:
    item = Item(organization_id=db.organization_id, kind=kind)
    db.add(item)
    await db.flush()
    return item.id


async def add_material(db: WriteSession, material: Material) -> Material:
    """Add ``material``, as a new item, of the default type unless it names one."""
    material.type_id = material.type_id or await default_type(db, CategoryKind.MATERIAL)
    material.id = await _item(db, ItemKind.MATERIAL)
    material.organization_id = db.organization_id
    db.add(material)
    await db.flush()
    return material


async def add_base(db: WriteSession, base: Base) -> Base:
    """Add ``base``, as a new item, of the default type unless it names one."""
    base.type_id = base.type_id or await default_type(db, CategoryKind.BASE)
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
    """Add ``product`` with its default variation, which a simple product
    sells, of the default type unless it names one."""
    product.type_id = product.type_id or await default_type(db, CategoryKind.PRODUCT)
    product.organization_id = db.organization_id
    db.add(product)
    await db.flush()
    variation = await add_variation(db, Variation(product_id=product.id, is_default=True))
    return product, variation


async def remove_item(db: WriteSession, item_id: int) -> None:
    """Remove a material, base or variation with its lots, stock and adjustments;
    the listings selling it sell nothing. One a purchase bought is kept:
    removing it raises the database's ``IntegrityError``."""
    await _unlist(db, [item_id])
    await db.execute(delete(Item).where(col(Item.id) == item_id))


async def remove_product(db: WriteSession, product: Product) -> None:
    """Remove ``product`` with its variations, as :func:`remove_item` does each."""
    variations = list(
        (await db.exec(select(Variation.id).where(Variation.product_id == product.id))).all()
    )
    await _unlist(db, variations)
    await db.execute(delete(Item).where(col(Item.id).in_(variations)))
    await db.delete(product)


async def _unlist(db: WriteSession, item_ids: list[int]) -> None:
    await db.execute(
        update(Listing)
        .where(col(Listing.item_id).in_(item_ids))
        .values(item_id=None, matched_by=None)
    )


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


async def category_of(db: AsyncSession, item_id: int) -> Category | None:
    """An item's category, if it has one. A variation's is its product's."""
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
    return await db.get(Category, category_id) if category_id is not None else None


async def picking(db: AsyncSession, item_id: int) -> Picking:
    """Which lot of an item goes first: its category's rule, or, for an item
    with no category, the earliest expiry first."""
    category = await category_of(db, item_id)
    return category.picking if category is not None else Picking.EARLIEST_EXPIRY


async def lot_scan(db: AsyncSession, item_id: int) -> LotScan:
    """How an item's shipment lines choose their lots: its category's rule,
    or by hand for an item with no category."""
    category = await category_of(db, item_id)
    return category.lot_scan if category is not None else LotScan.BY_HAND


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


async def transfer(
    db: WriteSession,
    item_id: int,
    from_location_id: int,
    to_location_id: int,
    quantity: Decimal,
    *,
    lot_id: int | None = None,
    dated_on: date,
) -> Transfer:
    """Move ``quantity`` of an item from one location to another on
    ``dated_on``: out of ``lot_id``, or, with none, out of what the location
    holds in no lot, and no more than either holds. Both halves go at the
    item's average cost, which the move leaves as it was (``hoard.costing``),
    and its listings are pushed what each location now holds."""
    if from_location_id == to_location_id:
        raise StockRefused(StockError.TRANSFER_SAME_LOCATION)
    # Locking the item first keeps two moves of its stock from both counting what is there.
    await db.exec(select(Item.id).where(Item.id == item_id).with_for_update())
    lot: Lot | None = None
    if lot_id is not None:
        lot = await db.get(Lot, lot_id, populate_existing=True)
        if lot is None or (lot.item_id, lot.location_id) != (item_id, from_location_id):
            raise StockRefused(StockError.TRANSFER_LOT_ELSEWHERE)
        held = lot.balance
    else:
        if await picking(db, item_id) is Picking.RECORDED:
            raise StockRefused(HoldError.LOT_REQUIRED)
        in_lots = await db.exec(
            select(func.coalesce(func.sum(func.greatest(Lot.balance, 0)), 0)).where(
                Lot.item_id == item_id, Lot.location_id == from_location_id
            )
        )
        held = await on_hand(db, item_id, from_location_id) - Decimal(in_lots.one())
    if quantity > held:
        raise StockRefused(StockError.TRANSFER_OVER_BALANCE)
    moved = Transfer(organization_id=db.organization_id)
    db.add(moved)
    await db.flush()
    into = None if lot is None else await _lot_at(db, lot, to_location_id)
    for location_id, change, at in (
        (from_location_id, -quantity, lot),
        (to_location_id, quantity, into),
    ):
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=item_id,
                location_id=location_id,
                lot_id=None if at is None else at.id,
                kind=AdjustmentKind.TRANSFER,
                dated_on=dated_on,
                change=change,
                transfer_id=moved.id,
            )
        )
    await db.flush()
    if lot is not None and into is not None:
        await _move_hold(db, lot, into, whole=quantity == held)
    await costing.rederive(db, item_id)
    return moved


async def _lot_at(db: WriteSession, lot: Lot, location_id: int) -> Lot:
    """``lot``'s lot at another location: the one an earlier transfer moved
    it into or brought it from, or a copy of it made now, with its
    properties and documents."""
    this, other = aliased(Adjustment), aliased(Adjustment)
    linked = (
        await db.exec(
            select(Lot)
            .join(other, col(other.lot_id) == Lot.id)
            .join(this, col(this.transfer_id) == col(other.transfer_id))
            .where(this.lot_id == lot.id, other.location_id == location_id)
            .order_by(col(Lot.id))
        )
    ).first()
    if linked is not None:
        return linked
    copy = Lot(
        **lot.model_dump(
            include={
                "organization_id",
                "item_id",
                "number",
                "dated_on",
                "expires_on",
                "notes",
                "allergens",
                "fragrance_allergens",
            }
        ),
        location_id=location_id,
    )
    db.add(copy)
    await db.flush()
    for value in (await db.exec(select(PropertyValue).where(PropertyValue.lot_id == lot.id))).all():
        db.add(
            PropertyValue(
                organization_id=db.organization_id,
                definition_id=value.definition_id,
                lot_id=copy.id,
                value=value.value,
            )
        )
    for link in (await db.exec(select(DocumentLink).where(DocumentLink.lot_id == lot.id))).all():
        db.add(
            DocumentLink(organization_id=db.organization_id, file_id=link.file_id, lot_id=copy.id)
        )
    await db.flush()
    return copy


async def _move_hold(db: WriteSession, lot: Lot, into: Lot, *, whole: bool) -> None:
    """Hold ``into`` as ``lot`` is held, a cure's ready date included: ``lot``'s
    own hold, when all of it moved, or the same hold beside it. A lot on hold
    already keeps its own."""
    hold = (
        await db.exec(select(Hold).where(Hold.lot_id == lot.id, col(Hold.ended).is_(None)))
    ).first()
    if (
        hold is None
        or (
            await db.exec(select(Hold.id).where(Hold.lot_id == into.id, col(Hold.ended).is_(None)))
        ).first()
    ):
        return
    if whole:
        hold.lot_id = into.id
        db.add(hold)
        await db.flush()
        return
    await place(
        db,
        into,
        hold.reason_id,
        ready_on=hold.ready_on,
        notes=hold.notes,
        run_id=hold.run_id,
        order_id=hold.order_id,
        from_hold_id=hold.id,
        quiet=True,
    )
