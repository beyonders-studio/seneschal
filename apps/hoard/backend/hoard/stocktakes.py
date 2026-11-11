"""Stocktakes: counting what is on hand at a location.

A stocktake counts materials or products: every one, or a random sample, of
one category or all. Its rows are confirmed one by one. Confirming a row
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
    CategoryKind,
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
    else:
        items = select(Variation.id).join(Product, col(Product.id) == col(Variation.product_id))
        if stocktake.category_id is not None:
            items = items.where(Product.category_id == stocktake.category_id)
    if stocktake.sample_size is not None:
        items = items.order_by(func.random()).limit(stocktake.sample_size)
    rows = [
        StocktakeRow(organization_id=db.organization_id, stocktake_id=stocktake.id, item_id=item)
        for item in (await db.exec(items)).all()
    ]
    db.add_all(rows)
    await db.flush()
    return rows


async def confirm_row(db: WriteSession, row: StocktakeRow, counted: Decimal) -> None:
    """Confirm ``row`` as ``counted``: what is on hand when it is first
    confirmed is what was expected, and the variance is its adjustment, at the
    stocktake's location and date."""
    stocktake = await db.get_one(Stocktake, row.stocktake_id)
    if row.expected is None:
        row.expected = await on_hand(db, row.item_id, stocktake.location_id)
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
