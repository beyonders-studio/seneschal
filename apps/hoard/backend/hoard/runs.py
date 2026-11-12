"""Production runs: their lines, filled from a recipe, and completing them.

A run makes a variation or a base from its recipe, in batches. Opening it
fills its lines from the recipe: each line's quantity times the planned
batches. The lines are the run's own from then on, so a later change to the
recipe leaves them as they are; changing the planned batches scales them.

A line consumes the lot someone recorded on it. Completing a run assumes a lot
for each line that names none: the item's lots at the run's location that hold
stock, the earliest expiry first, then the first in. A line drawing on more
than one lot is split, one line per lot, each marked assumed.

Only completing a run moves stock: each line out, and the quantity made in,
into its output lot, numbered with the run's code. ``hoard.costing`` costs it.
A completed run can be reopened, which takes those adjustments and the output
lot back and returns its assumed lines to naming no lot.

A run can also be made at once (:func:`make`), opened and completed together,
as auto-manufacture makes what a store order sells. Such a run records no
events.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func
from sqlmodel import col, or_, select

from hoard import costing, events
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    Lot,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RecipeLine,
    RunStatus,
)
from hoard.stock import StockError, draw_lots
from seneschal_core.db import WriteSession


class RunError(StrEnum):
    """Why a change to a production run was refused: codes pages translate."""

    #: What it makes has no recipe to fill its lines from.
    RUN_NO_RECIPE = "RUN_NO_RECIPE"
    #: The run is completed: what it moved changes only once it is reopened.
    RUN_COMPLETED = "RUN_COMPLETED"
    #: The run is not completed, so there is nothing to reopen.
    RUN_NOT_COMPLETED = "RUN_NOT_COMPLETED"
    #: The run has already started.
    RUN_STARTED = "RUN_STARTED"
    #: Another run has the code.
    RUN_CODE_TAKEN = "RUN_CODE_TAKEN"
    #: The lot is not one of the line's item at the run's location.
    RUN_LOT_MISMATCH = "RUN_LOT_MISMATCH"


class RunRefused(Exception):
    def __init__(self, code: RunError | StockError) -> None:
        super().__init__(code.value)
        self.code = code


async def recipe_for(db: WriteSession, item_id: int) -> Recipe | None:
    """The recipe that makes ``item_id``, a variation or a base."""
    found = await db.exec(
        select(Recipe).where(or_(Recipe.variation_id == item_id, Recipe.base_id == item_id))
    )
    return found.first()


async def lines(db: WriteSession, run_id: int) -> list[ProductionRunLine]:
    """A run's lines, in the order they were filled."""
    found = await db.exec(
        select(ProductionRunLine)
        .where(ProductionRunLine.run_id == run_id)
        .order_by(col(ProductionRunLine.id))
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def next_code(db: WriteSession) -> str:
    """The next free ``R-`` code."""
    latest = (await db.exec(select(func.max(ProductionRun.id)))).one()
    return f"R-{(latest or 0) + 1:04d}"


async def open_run(db: WriteSession, run: ProductionRun, *, quiet: bool = False) -> ProductionRun:
    """Add ``run``, not started or in progress, with its lines from its item's
    recipe times its planned batches. A ``quiet`` run records no event."""
    recipe = await recipe_for(db, run.item_id)
    if recipe is None:
        raise RunRefused(RunError.RUN_NO_RECIPE)
    run.organization_id = db.organization_id
    run.recipe_id = recipe.id
    if run.status is RunStatus.IN_PROGRESS:
        run.started_on = run.started_on or date.today()
    db.add(run)
    await db.flush()
    recipe_lines = await db.exec(
        select(RecipeLine)
        .where(RecipeLine.recipe_id == recipe.id)
        .order_by(col(RecipeLine.position), col(RecipeLine.id))
    )
    for line in recipe_lines.all():
        db.add(
            ProductionRunLine(
                organization_id=db.organization_id,
                run_id=run.id,
                item_id=line.item_id,
                quantity=(line.quantity * run.planned_batches).quantize(PLACES),
            )
        )
    await db.flush()
    if run.status is RunStatus.IN_PROGRESS and not quiet:
        await events.record(db, await events.run_event(db, events.EventName.RUN_STARTED, run))
    return run


def not_completed(run: ProductionRun) -> None:
    """Refuse a change to what a completed run moved."""
    if run.status is RunStatus.COMPLETED:
        raise RunRefused(RunError.RUN_COMPLETED)


async def plan_batches(db: WriteSession, run: ProductionRun, batches: Decimal) -> None:
    """Plan ``batches`` for a run not yet completed, scaling its lines."""
    not_completed(run)
    for line in await lines(db, run.id):
        line.quantity = (line.quantity * batches / run.planned_batches).quantize(PLACES)
    run.planned_batches = batches
    await db.flush()


async def record_lot(
    db: WriteSession, run: ProductionRun, line: ProductionRunLine, lot_id: int | None
) -> None:
    """Record the lot a line consumes, or take it off so that one is assumed."""
    not_completed(run)
    if lot_id is not None:
        lot = await db.get_one(Lot, lot_id)
        if (lot.item_id, lot.location_id) != (line.item_id, run.location_id):
            raise RunRefused(RunError.RUN_LOT_MISMATCH)
    line.lot_id = lot_id
    line.assumed = False
    await db.flush()


async def start(db: WriteSession, run: ProductionRun, started_on: date) -> None:
    """Start a run that has not started."""
    if run.status is not RunStatus.NOT_STARTED:
        raise RunRefused(RunError.RUN_STARTED)
    run.status = RunStatus.IN_PROGRESS
    run.started_on = started_on
    await db.flush()
    await events.record(db, await events.run_event(db, events.EventName.RUN_STARTED, run))


def _line(db: WriteSession, run: ProductionRun, like: ProductionRunLine) -> ProductionRunLine:
    """A new line of ``run`` consuming what ``like`` does."""
    line = ProductionRunLine(
        organization_id=db.organization_id, run_id=run.id, item_id=like.item_id
    )
    db.add(line)
    return line


async def _assume_lots(db: WriteSession, run: ProductionRun) -> list[ProductionRunLine]:
    """Assume a lot for each of a run's lines that names none, splitting a line
    over as many lots as it takes. Answers the lines."""
    found = await lines(db, run.id)
    # What the run's lines draw from each lot, so that no lot is assumed past its balance.
    drawn: dict[int, Decimal] = defaultdict(Decimal)
    for line in found:
        if line.lot_id is not None:
            drawn[line.lot_id] += line.quantity
    picked: list[ProductionRunLine] = []
    for line in found:
        if line.lot_id is not None:
            picked.append(line)
            continue
        current: ProductionRunLine | None = line
        for lot_id, take in await draw_lots(
            db, line.item_id, run.location_id, line.quantity, drawn
        ):
            current = current or _line(db, run, line)
            current.lot_id, current.quantity, current.assumed = lot_id, take, lot_id is not None
            picked.append(current)
            current = None
    await db.flush()
    return picked


async def complete(
    db: WriteSession,
    run: ProductionRun,
    actual_quantity: Decimal,
    completed_on: date,
    *,
    quiet: bool = False,
) -> None:
    """Complete ``run``, having made ``actual_quantity`` on ``completed_on``:
    its lines out, from their lots, and what it made into its output lot. A
    ``quiet`` run records no event."""
    not_completed(run)
    for line in await _assume_lots(db, run):
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=line.item_id,
                location_id=run.location_id,
                lot_id=line.lot_id,
                kind=AdjustmentKind.PRODUCTION,
                dated_on=completed_on,
                change=-line.quantity,
                production_run_id=run.id,
                production_run_line_id=line.id,
            )
        )
    lot = Lot(
        organization_id=db.organization_id,
        item_id=run.item_id,
        location_id=run.location_id,
        number=run.code,
        dated_on=completed_on,
        expires_on=run.expires_on,
    )
    db.add(lot)
    await db.flush()
    if actual_quantity > 0:
        db.add(
            Adjustment(
                organization_id=db.organization_id,
                item_id=run.item_id,
                location_id=run.location_id,
                lot_id=lot.id,
                kind=AdjustmentKind.PRODUCTION,
                dated_on=completed_on,
                change=actual_quantity,
                production_run_id=run.id,
            )
        )
    run.status = RunStatus.COMPLETED
    run.actual_quantity = actual_quantity
    run.completed_on = completed_on
    run.started_on = run.started_on or completed_on
    run.output_lot_id = lot.id
    await costing.rederive_run(db, run)
    if not quiet:
        await events.record(db, await events.run_event(db, events.EventName.RUN_COMPLETED, run))


async def make(
    db: WriteSession,
    item_id: int,
    location_id: int,
    quantity: Decimal,
    made_on: date,
    *,
    initiative_id: int | None = None,
) -> ProductionRun | None:
    """Make ``quantity`` of a variation or a base at a location on
    ``made_on``, by a run opened and completed at once from its recipe, which
    records no events. None when it has no recipe, or too little is asked for
    to plan a batch."""
    recipe = await recipe_for(db, item_id)
    if recipe is None:
        return None
    batches = (quantity / recipe.batch_quantity).quantize(PLACES)
    if batches <= 0:
        return None
    run = ProductionRun(
        code=await next_code(db),
        item_id=item_id,
        location_id=location_id,
        initiative_id=initiative_id,
        status=RunStatus.IN_PROGRESS,
        started_on=made_on,
        planned_batches=batches,
    )
    await open_run(db, run, quiet=True)
    await complete(db, run, quantity, made_on, quiet=True)
    return run


async def reopen(db: WriteSession, run: ProductionRun) -> None:
    """Reopen a completed run: its adjustments and its output lot go, and its
    assumed lines name no lot again. Refused while anything else draws on the
    output lot."""
    if run.status is not RunStatus.COMPLETED:
        raise RunRefused(RunError.RUN_NOT_COMPLETED)
    lot_id = run.output_lot_id
    others = await db.exec(
        select(func.count())
        .select_from(Adjustment)
        .where(
            Adjustment.lot_id == lot_id,
            col(Adjustment.production_run_id).is_distinct_from(run.id),
        )
    )
    if others.one():
        raise RunRefused(StockError.LOT_IN_USE)
    await db.execute(delete(Adjustment).where(col(Adjustment.production_run_id) == run.id))
    run.status = RunStatus.IN_PROGRESS
    run.actual_quantity = run.completed_on = run.output_lot_id = None
    await db.flush()
    await db.execute(delete(Lot).where(col(Lot.id) == lot_id))
    # Each item's assumed lines become one line again, naming no lot.
    kept: dict[int, ProductionRunLine] = {}
    for line in await lines(db, run.id):
        if line.lot_id is not None and not line.assumed:
            continue
        if line.item_id in kept:
            kept[line.item_id].quantity += line.quantity
            await db.delete(line)
        else:
            line.lot_id, line.assumed = None, False
            kept[line.item_id] = line
    await costing.rederive_run(db, run)


async def delete_run(db: WriteSession, run: ProductionRun) -> None:
    """Delete a run that is not completed."""
    not_completed(run)
    await db.delete(run)
    await db.flush()
