"""Stocktakes: counts of stock at a location, whose variances become adjustments.

A stocktake counts materials or products, every one or a random sample, of
one category or all (``hoard.stocktakes``). Its rows are confirmed one by one;
it is finished once every row is, and its accuracy is the share of confirmed
rows whose count matched.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Subquery, and_, func
from sqlmodel import col, select

from hoard import stocktakes
from hoard.api.adjustments import (
    NOT_FOUND,
    NOTES,
    AdjustmentItem,
    NonNegative,
    describe_items,
    known,
    location_or_main,
)
from hoard.api.deps import HoardError
from hoard.api.shapes import Page, PageDep
from hoard.records import (
    Category,
    CategoryKind,
    Lot,
    LotCount,
    StockLevel,
    Stocktake,
    StocktakeRow,
)
from hoard.stocktakes import StocktakeRefused
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import OrganizationSession

router = APIRouter(prefix="/stocktakes", tags=["stocktakes"])


class StocktakeError(StrEnum):
    """Why a stocktake was refused."""

    #: The category sorts the other kind of record.
    STOCKTAKE_CATEGORY_KIND = "STOCKTAKE_CATEGORY_KIND"
    #: The stocktake counts each item as one, so a lot isn't added to it.
    STOCKTAKE_NOT_BY_LOT = "STOCKTAKE_NOT_BY_LOT"
    #: The lot is at another location, or of another kind of item.
    STOCKTAKE_LOT_MISMATCH = "STOCKTAKE_LOT_MISMATCH"


class StocktakeLot(BaseModel):
    id: int
    number: str
    expires_on: date | None


class StocktakeRowRead(BaseModel):
    id: int
    item: AdjustmentItem
    #: The lot it counts; null for the item's stock in no lot, or the whole
    #: item in a stocktake that counts each item as one.
    lot: StocktakeLot | None
    #: What it would count now: its lot's balance, the item's stock in no lot,
    #: or all the item holds at the stocktake's location.
    on_hand: Decimal
    #: What was on hand when the row was first confirmed; null until then.
    expected: Decimal | None
    counted: Decimal | None


class StocktakeSummary(BaseModel):
    id: int
    kind: CategoryKind
    location_id: int
    category_id: int | None
    #: How many items it samples at random; null when it counts every one.
    sample_size: int | None
    #: How it counts lots: a full count, a spot check, or null, each item as one.
    lot_count: LotCount | None
    dated_on: date
    notes: str | None
    row_count: int
    confirmed_count: int
    #: The share of confirmed rows whose count matched, from 0 to 1; null
    #: until a row is confirmed.
    accuracy: Decimal | None
    #: Whether every row is confirmed.
    finished: bool
    created_at: datetime
    created_by: str | None


class StocktakeRead(StocktakeSummary):
    rows: list[StocktakeRowRead]


class StocktakeCount(BaseModel):
    """What a stocktake expected of one item and counted."""

    #: What was on hand when its row was first confirmed; null until then.
    expected: Decimal | None
    counted: Decimal | None


class StocktakeListed(StocktakeSummary):
    #: The item's count, when the list is of the stocktakes counting one item.
    count: StocktakeCount | None


def _counts() -> Subquery:
    return (
        select(
            StocktakeRow.stocktake_id,
            func.count().label("rows"),
            func.count(col(StocktakeRow.counted)).label("confirmed"),
            func.count()
            .filter(col(StocktakeRow.counted) == col(StocktakeRow.expected))
            .label("matched"),
        )
        .group_by(col(StocktakeRow.stocktake_id))
        .subquery()
    )


def _summary(stocktake: Stocktake, rows: int, confirmed: int, matched: int) -> StocktakeSummary:
    return StocktakeSummary(
        id=stocktake.id,
        kind=stocktake.kind,
        location_id=stocktake.location_id,
        category_id=stocktake.category_id,
        sample_size=stocktake.sample_size,
        lot_count=stocktake.lot_count,
        dated_on=stocktake.dated_on,
        notes=stocktake.notes,
        row_count=rows,
        confirmed_count=confirmed,
        accuracy=(Decimal(matched) / confirmed).quantize(stocktakes.ACCURACY_PLACES)
        if confirmed
        else None,
        finished=confirmed == rows,
        created_at=stocktake.created_at,
        created_by=stocktake.created_by,
    )


@router.get("")
async def list_stocktakes(
    db: OrganizationSessionDep,
    page: PageDep,
    item_id: Annotated[int | None, Query()] = None,
) -> Page[StocktakeListed]:
    """Stocktakes, the latest first, with how far each has got and its
    accuracy: those with a row for one item, with its count, when asked."""
    counts = _counts()
    query: Any = select(Stocktake, counts.c.rows, counts.c.confirmed, counts.c.matched)
    counted = select(func.count()).select_from(Stocktake)
    if item_id is not None:
        of_item = and_(
            col(StocktakeRow.stocktake_id) == Stocktake.id, col(StocktakeRow.item_id) == item_id
        )
        query = query.add_columns(StocktakeRow).join(StocktakeRow, of_item)
        counted = counted.join(StocktakeRow, of_item)
    rows = await db.exec(
        query.outerjoin(counts, counts.c.stocktake_id == col(Stocktake.id))
        .order_by(col(Stocktake.dated_on).desc(), col(Stocktake.id).desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    return Page(
        items=[
            StocktakeListed(
                **_summary(stocktake, count or 0, confirmed or 0, matched or 0).model_dump(),
                count=None
                if not row
                else StocktakeCount(expected=row[0].expected, counted=row[0].counted),
            )
            for stocktake, count, confirmed, matched, *row in rows.all()
        ],
        total=(await db.exec(counted)).one(),
    )


async def _read(db: OrganizationSession, stocktake: Stocktake) -> StocktakeRead:
    rows = list(
        (
            await db.exec(
                select(StocktakeRow)
                .where(StocktakeRow.stocktake_id == stocktake.id)
                .order_by(col(StocktakeRow.id))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    items = await describe_items(db, [row.item_id for row in rows])
    levels = await db.exec(
        select(col(StockLevel.item_id), col(StockLevel.quantity)).where(
            StockLevel.location_id == stocktake.location_id,
            col(StockLevel.item_id).in_([row.item_id for row in rows]),
        )
    )
    on_hand = dict(levels.all())
    lots = {
        lot.id: lot
        for lot in (
            await db.exec(
                select(Lot).where(col(Lot.id).in_([row.lot_id for row in rows if row.lot_id]))
            )
        ).all()
    }
    outside: dict[int, Decimal] = {}
    if stocktake.lot_count is not None:
        in_lots = await db.exec(
            select(col(Lot.item_id), func.sum(col(Lot.balance)))
            .where(
                Lot.location_id == stocktake.location_id,
                col(Lot.item_id).in_([row.item_id for row in rows]),
            )
            .group_by(col(Lot.item_id))
        )
        held = dict(in_lots.all())
        outside = {
            row.item_id: on_hand.get(row.item_id, Decimal(0)) - Decimal(held.get(row.item_id, 0))
            for row in rows
        }

    def now(row: StocktakeRow) -> Decimal:
        if row.lot_id is not None:
            lot = lots.get(row.lot_id)
            return lot.balance if lot is not None else Decimal(0)
        if stocktake.lot_count is not None:
            return outside.get(row.item_id, Decimal(0))
        return on_hand.get(row.item_id, Decimal(0))

    confirmed = [row for row in rows if row.counted is not None]
    return StocktakeRead(
        **_summary(
            stocktake,
            len(rows),
            len(confirmed),
            sum(1 for row in confirmed if row.counted == row.expected),
        ).model_dump(),
        rows=[
            StocktakeRowRead(
                id=row.id,
                item=items[row.item_id],
                lot=StocktakeLot(
                    id=lots[row.lot_id].id,
                    number=lots[row.lot_id].number,
                    expires_on=lots[row.lot_id].expires_on,
                )
                if row.lot_id in lots
                else None,
                on_hand=now(row),
                expected=row.expected,
                counted=row.counted,
            )
            for row in rows
        ],
    )


@router.get("/{stocktake_id}", responses=NOT_FOUND)
async def read_stocktake(db: OrganizationSessionDep, stocktake_id: int) -> StocktakeRead:
    """A stocktake and its rows."""
    return await _read(db, await known(db, Stocktake, stocktake_id))


class StocktakeCreate(BaseModel):
    kind: CategoryKind
    #: The main location when not given.
    location_id: int | None = None
    #: Every category when not given.
    category_id: int | None = None
    #: How many items to sample at random; every one when not given.
    sample_size: Annotated[int, Field(gt=0)] | None = None
    #: How to count lots: a full count or a spot check; each item as one when
    #: not given.
    lot_count: LotCount | None = None
    #: Today when not given.
    dated_on: date | None = None
    notes: str | None = NOTES


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={**NOT_FOUND, 422: {"description": "STOCKTAKE_CATEGORY_KIND"}},
)
async def create_stocktake(db: WriteSessionDep, body: StocktakeCreate) -> StocktakeRead:
    """Start a stocktake, with a row for each item it counts."""
    if body.category_id is not None:
        category = await known(db, Category, body.category_id)
        if category.kind != body.kind:
            raise refuse(
                status.HTTP_422_UNPROCESSABLE_CONTENT, StocktakeError.STOCKTAKE_CATEGORY_KIND
            )
    stocktake = Stocktake(
        kind=body.kind,
        location_id=await location_or_main(db, body.location_id),
        category_id=body.category_id,
        sample_size=body.sample_size,
        lot_count=body.lot_count,
        dated_on=body.dated_on or date.today(),
        notes=body.notes,
    )
    await stocktakes.start_stocktake(db, stocktake)
    await db.commit()
    return await _read(db, stocktake)


class StocktakeRowConfirm(BaseModel):
    #: What was counted.
    counted: NonNegative


@router.post("/{stocktake_id}/rows/{row_id}/confirm", responses=NOT_FOUND)
async def confirm_stocktake_row(
    db: WriteSessionDep, stocktake_id: int, row_id: int, body: StocktakeRowConfirm
) -> StocktakeRead:
    """Confirm a row as counted: a difference from what was on hand becomes
    its adjustment."""
    stocktake = await known(db, Stocktake, stocktake_id)
    row = await known(db, StocktakeRow, row_id)
    if row.stocktake_id != stocktake.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    await stocktakes.confirm_row(db, row, body.counted)
    await db.commit()
    return await _read(db, stocktake)


class StocktakeLotAdd(BaseModel):
    """A lot found while counting, to count in the stocktake."""

    lot_id: int


@router.post(
    "/{stocktake_id}/rows",
    responses={
        **NOT_FOUND,
        409: {"description": "STOCKTAKE_NOT_BY_LOT"},
        422: {"description": "STOCKTAKE_LOT_MISMATCH"},
    },
)
async def add_stocktake_lot(
    db: WriteSessionDep, stocktake_id: int, body: StocktakeLotAdd
) -> StocktakeRead:
    """Count a lot the stocktake doesn't list, found while counting; one it
    lists already is left as it is."""
    stocktake = await known(db, Stocktake, stocktake_id)
    lot = await known(db, Lot, body.lot_id)
    try:
        await stocktakes.add_lot(db, stocktake, lot)
    except StocktakeRefused as refused:
        code = StocktakeError(refused.code)
        status_code = (
            status.HTTP_409_CONFLICT
            if code is StocktakeError.STOCKTAKE_NOT_BY_LOT
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise refuse(status_code, code) from None
    await db.commit()
    return await _read(db, stocktake)


@router.post("/{stocktake_id}/finish", responses=NOT_FOUND)
async def finish_stocktake(db: WriteSessionDep, stocktake_id: int) -> StocktakeRead:
    """Finish a stocktake: in a full count, each lot of an item counted that
    wasn't counted is confirmed as nothing. A spot check changes nothing."""
    stocktake = await known(db, Stocktake, stocktake_id)
    await stocktakes.finish(db, stocktake)
    await db.commit()
    return await _read(db, stocktake)
