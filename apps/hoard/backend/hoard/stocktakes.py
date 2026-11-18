"""Stocktakes: counting what is on hand at a location.

A stocktake counts materials or products: every one, or a random sample, of
one category or all. It counts each item as one, or each lot of it at its
location (``Stocktake.lot_count``), chosen when it is started: a full count,
where finishing it writes the lots of a counted item that weren't counted down
to nothing, or a spot check, where only the lots counted change. Counting by
lot, an item's stock in no lot is a row of its own, and a lot found while
counting is added to it. Its rows are confirmed one by one. Confirming a row
records what was on hand and what was counted, and a difference between them
becomes the row's stocktake adjustment. Confirming it again with a new count
changes that adjustment, or removes it once the count matches.

Its accuracy is the share of confirmed rows whose count matched.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlmodel import col, select

from hoard import costing
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Base,
    CategoryKind,
    Item,
    ItemKind,
    Lot,
    LotCount,
    Material,
    Product,
    Stocktake,
    StocktakeRow,
    Variation,
)
from hoard.stock import on_hand
from seneschal_core.db import OrganizationSession, WriteSession

#: The places an accuracy is kept to.
ACCURACY_PLACES = Decimal("0.0001")


async def start_stocktake(db: WriteSession, stocktake: Stocktake) -> list[StocktakeRow]:
    """Add ``stocktake`` with a row for each item it counts. Materials that are
    not inventoriable carry no stock, and are not counted."""
    stocktake.organization_id = db.organization_id
    db.add(stocktake)
    await db.flush()
    if stocktake.kind == CategoryKind.MATERIAL:
        items = select(Material.id).where(col(Material.inventoriable))
        if stocktake.category_id is not None:
            items = items.where(Material.category_id == stocktake.category_id)
    elif stocktake.kind == CategoryKind.BASE:
        items = select(Base.id)
        if stocktake.category_id is not None:
            items = items.where(Base.category_id == stocktake.category_id)
    else:
        items = select(Variation.id).join(Product, col(Product.id) == col(Variation.product_id))
        if stocktake.category_id is not None:
            items = items.where(Product.category_id == stocktake.category_id)
    if stocktake.sample_size is not None:
        items = items.order_by(func.random()).limit(stocktake.sample_size)
    rows: list[StocktakeRow] = []
    for item_id in (await db.exec(items)).all():
        lots: list[int | None] = [None]
        if stocktake.lot_count is not None:
            held = await _lots_held(db, item_id, stocktake.location_id)
            outside = await _outside_lots(db, item_id, stocktake.location_id)
            # Each lot holding stock there, and its stock in no lot when it has
            # some, or when it has nothing at all, so it is counted still.
            lots = [*held, *([None] if outside or not held else [])]
        rows += [
            StocktakeRow(
                organization_id=db.organization_id,
                stocktake_id=stocktake.id,
                item_id=item_id,
                lot_id=lot_id,
            )
            for lot_id in lots
        ]
    db.add_all(rows)
    await db.flush()
    return rows


async def _lots_held(db: WriteSession, item_id: int, location_id: int) -> list[int]:
    """The item's lots holding stock at a location, oldest first."""
    found = await db.exec(
        select(Lot.id)
        .where(Lot.item_id == item_id, Lot.location_id == location_id, Lot.balance != 0)
        .order_by(col(Lot.dated_on), col(Lot.id))
    )
    return list(found.all())


async def _outside_lots(db: WriteSession, item_id: int, location_id: int) -> Decimal:
    """What the item holds at a location in no lot."""
    in_lots = (
        await db.exec(
            select(func.coalesce(func.sum(Lot.balance), 0)).where(
                Lot.item_id == item_id, Lot.location_id == location_id
            )
        )
    ).one()
    return await on_hand(db, item_id, location_id) - Decimal(in_lots)


async def expected_of(db: WriteSession, stocktake: Stocktake, row: StocktakeRow) -> Decimal:
    """What a row would count now: its lot's balance, the item's stock in no
    lot, or, counting each item as one, all it holds at the location."""
    if row.lot_id is not None:
        return (await db.get_one(Lot, row.lot_id)).balance
    if stocktake.lot_count is not None:
        return await _outside_lots(db, row.item_id, stocktake.location_id)
    return await on_hand(db, row.item_id, stocktake.location_id)


class StocktakeRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: The kinds of item each kind of stocktake counts.
_COUNTS = {
    CategoryKind.MATERIAL: ItemKind.MATERIAL,
    CategoryKind.BASE: ItemKind.BASE,
    CategoryKind.PRODUCT: ItemKind.VARIATION,
}


async def add_lot(db: WriteSession, stocktake: Stocktake, lot: Lot) -> StocktakeRow:
    """The row counting ``lot``, added when the stocktake doesn't list it: a
    lot found while counting. Refused for a stocktake that counts each item as
    one (``STOCKTAKE_NOT_BY_LOT``), and for a lot at another location or of
    another kind of item than it counts (``STOCKTAKE_LOT_MISMATCH``)."""
    if stocktake.lot_count is None:
        raise StocktakeRefused("STOCKTAKE_NOT_BY_LOT")
    item = await db.get_one(Item, lot.item_id)
    if lot.location_id != stocktake.location_id or item.kind != _COUNTS[stocktake.kind]:
        raise StocktakeRefused("STOCKTAKE_LOT_MISMATCH")
    found = (
        await db.exec(
            select(StocktakeRow).where(
                StocktakeRow.stocktake_id == stocktake.id, StocktakeRow.lot_id == lot.id
            )
        )
    ).first()
    if found is not None:
        return found
    row = StocktakeRow(
        organization_id=db.organization_id,
        stocktake_id=stocktake.id,
        item_id=lot.item_id,
        lot_id=lot.id,
    )
    db.add(row)
    await db.flush()
    return row


async def finish(db: WriteSession, stocktake: Stocktake) -> int:
    """Finish a full count: each lot of an item with a counted row that
    wasn't counted is confirmed as nothing. Answers how many it confirmed; a
    spot check, and a stocktake by item, confirm none."""
    if stocktake.lot_count is not LotCount.FULL:
        return 0
    rows = (
        await db.exec(select(StocktakeRow).where(StocktakeRow.stocktake_id == stocktake.id))
    ).all()
    counted = {row.item_id for row in rows if row.counted is not None}
    left = [row for row in rows if row.counted is None and row.item_id in counted]
    for row in left:
        await confirm_row(db, row, Decimal(0))
    return len(left)


async def confirm_row(db: WriteSession, row: StocktakeRow, counted: Decimal) -> None:
    """Confirm ``row`` as ``counted``: what is on hand when it is first
    confirmed is what was expected, and the variance is its adjustment, at the
    stocktake's location and date."""
    stocktake = await db.get_one(Stocktake, row.stocktake_id)
    if row.expected is None:
        row.expected = await expected_of(db, stocktake, row)
    row.counted = counted
    variance = counted - row.expected
    adjustment = (
        await db.exec(select(Adjustment).where(Adjustment.stocktake_row_id == row.id))
    ).first()
    if variance == 0:
        if adjustment is not None:
            await db.delete(adjustment)
    elif adjustment is not None:
        adjustment.change = variance
    else:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=row.item_id,
                location_id=stocktake.location_id,
                lot_id=row.lot_id,
                kind=AdjustmentKind.STOCKTAKE,
                dated_on=stocktake.dated_on,
                change=variance,
                stocktake_row_id=row.id,
            )
        )
    await costing.rederive(db, row.item_id)


async def accuracy(db: OrganizationSession, stocktake_id: int) -> Decimal | None:
    """The share of the stocktake's confirmed rows whose count matched, from 0
    to 1; None until a row is confirmed."""
    rows = (
        await db.exec(
            select(StocktakeRow).where(
                StocktakeRow.stocktake_id == stocktake_id, col(StocktakeRow.counted).is_not(None)
            )
        )
    ).all()
    if not rows:
        return None
    matched = sum(1 for row in rows if row.counted == row.expected)
    return (Decimal(matched) / len(rows)).quantize(ACCURACY_PLACES)
