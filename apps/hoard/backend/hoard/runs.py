"""Production runs: their lines, filled from a recipe, and completing them.

A run makes a variation or a base from its recipe, in batches. Opening it
fills its lines from the recipe: each line's quantity times the planned
batches. A run can instead make as much as an amount of one of its lines
allows: it plans the batches that take exactly that amount (``hoard.units.use``).
The lines are the run's own from then on, so a later change to the recipe
leaves them as they are; changing the planned batches scales them.

What a run would take is planned beside what is **usable** at its location
(``hoard.summary``): what is available, less what lots past their expiry date
hold, since nobody plans to use them up. A line needing more is short by the
difference, which completing does not refuse. The most batches what is usable
covers is what the item's potential counts there too.

A line consumes the lot someone recorded on it. Completing a run assumes a lot
for each line that names none: the item's lots at the run's location that hold
stock and are not on hold, in the order its category's picking rule takes them
(``hoard.stock.draw_lots``). A line drawing on more than one lot is split, one
line per lot, each marked assumed. A lot on hold is never consumed, and an
item whose category records every lot is consumed only from a lot a line
names (``hoard.holds``).

Completing a run first makes the bases its lines take that are set to
auto-manufacture (:class:`~hoard.records.AutoManufacture`), each by a run made
at once (:func:`make`) at its location, on its completion day and in its
initiative: all a line takes, or, for only what is out of stock, what it takes
beyond what is usable there. Their lots are then what the lines are assumed to
draw. A base inside a base is made the same way, as that run completes. A line
naming a lot makes nothing, and a base that can't be made, with no recipe or
too little to plan a batch, goes short: making one never refuses the run.
Reopening the run leaves the runs that made them completed.

Only completing a run moves stock: each line out, and the quantity made in,
into its output lot, numbered with the run's code. ``hoard.costing`` costs it.
When what it made cures, its lot goes on hold, curing, until its cure time
has passed.
A completed run can be reopened, which takes those adjustments and the output
lot back and returns its assumed lines to naming no lot.

A run can also be made at once (:func:`make`), opened and completed together,
as auto-manufacture makes what a store order sells, or a base a run uses. Such a run records no
events, and names no lot of what it consumes.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import delete, func
from sqlmodel import col, or_, select

from hoard import codes, costing, events, holds, recipes, summary, units
from hoard.holds import HoldError
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    AutoManufacture,
    Base,
    Lot,
    ProductionRun,
    ProductionRunLine,
    Recipe,
    RecipeLine,
    RunStatus,
)
from hoard.stock import StockError, draw_lots
from hoard.units import UnitError, UnitRefused
from seneschal_core.db import OrganizationSession, WriteSession


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
    #: The item to make as much as it allows is not one of its recipe's lines.
    RUN_NOT_IN_RECIPE = "RUN_NOT_IN_RECIPE"


class RunRefused(Exception):
    def __init__(
        self, code: RunError | StockError | HoldError | UnitError | recipes.RecipeError
    ) -> None:
        super().__init__(code.value)
        self.code = code


async def recipe_for(db: OrganizationSession, item_id: int) -> Recipe | None:
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
    return await codes.next_code(db, ProductionRun, "R")


async def _recipe_lines(db: OrganizationSession, recipe: Recipe) -> list[RecipeLine]:
    found = await db.exec(
        select(RecipeLine)
        .where(RecipeLine.recipe_id == recipe.id)
        .order_by(col(RecipeLine.position), col(RecipeLine.id))
    )
    return list(found.all())


@dataclass(frozen=True)
class Planned:
    """What a run would take of its recipe: its batches, kept to six places,
    what they make, and each line's quantity."""

    batches: Decimal
    makes: Decimal
    lines: list[RecipeLine]
    quantities: list[Decimal]


async def planned(
    db: OrganizationSession,
    recipe: Recipe,
    batches: Decimal,
    use: tuple[int, Decimal] | None,
) -> Planned:
    """``recipe``'s lines over ``batches``; or, with ``use``, an item among
    them and an amount of it in its tracking unit, over the batches that take
    exactly that amount of it, the other lines scaled from what was written
    (``hoard.units.use``)."""
    lines = await _recipe_lines(db, recipe)
    try:
        if use is None:
            quantities = [units.scaled(line.quantity, batches) for line in lines]
        else:
            item_id, amount = use
            index = next((at for at, line in enumerate(lines) if line.item_id == item_id), None)
            if index is None:
                raise RunRefused(RunError.RUN_NOT_IN_RECIPE)
            written = [recipes.Line(line.item_id, line.written, line.unit_id) for line in lines]
            worked = await recipes.work_out(db, recipe, written)
            took = units.use(worked.figures, index, amount)
            batches, quantities = took.batches, took.quantities
        kept = units.kept(batches)
        if any(quantity <= 0 for quantity in quantities):
            raise UnitRefused(UnitError.AMOUNT_TOO_SMALL)
    except (UnitRefused, recipes.RecipeRefused) as refused:
        raise RunRefused(refused.code) from None
    return Planned(kept, units.scaled(recipe.batch_quantity, batches), lines, quantities)


async def open_run(
    db: WriteSession,
    run: ProductionRun,
    *,
    quiet: bool = False,
    use: tuple[int, Decimal] | None = None,
) -> ProductionRun:
    """Add ``run``, not started or in progress, with its lines from its item's
    recipe times its planned batches, or, with ``use``, the batches that take
    exactly an amount of one line (:func:`planned`). A ``quiet`` run records
    no event."""
    recipe = await recipe_for(db, run.item_id)
    if recipe is None:
        raise RunRefused(RunError.RUN_NO_RECIPE)
    plan = await planned(db, recipe, run.planned_batches, use)
    run.organization_id = db.organization_id
    run.recipe_id = recipe.id
    run.planned_batches = plan.batches
    if run.status is RunStatus.IN_PROGRESS:
        run.started_on = run.started_on or date.today()
    db.add(run)
    await db.flush()
    for line, quantity in zip(plan.lines, plan.quantities, strict=True):
        db.add(
            ProductionRunLine(
                organization_id=db.organization_id,
                run_id=run.id,
                item_id=line.item_id,
                quantity=quantity,
            )
        )
    await db.flush()
    if run.status is RunStatus.IN_PROGRESS and not quiet:
        await events.record(db, await events.run_event(db, events.EventName.RUN_STARTED, run))
    return run


@dataclass(frozen=True)
class PlanLine:
    item_id: int
    quantity: Decimal
    #: What is usable at the run's location.
    usable: Decimal
    #: How much more it takes than is usable; None when it is not short.
    short: Decimal | None


@dataclass(frozen=True)
class Plan:
    """A run as it would be opened, beside what is usable at its location."""

    batches: Decimal
    #: What it makes, in its output's unit.
    makes: Decimal
    lines: list[PlanLine]
    #: The most batches what is usable makes: the scarcest line's; None for a
    #: recipe with no lines.
    most: Decimal | None
    #: With ``use``, while a line is short, the most of the named item those
    #: batches use.
    most_use: Decimal | None = None


def short(quantity: Decimal, can_use: Decimal) -> Decimal | None:
    """How much more than ``can_use`` a line takes, or None when it is not short."""
    return quantity - can_use if quantity > can_use else None


async def plan(
    db: OrganizationSession,
    item_id: int,
    location_id: int,
    batches: Decimal,
    use: tuple[int, Decimal] | None,
    today: date,
) -> Plan:
    """What a run of ``item_id`` at ``location_id`` would take, as
    :func:`open_run` would plan it, each line beside what is usable there."""
    recipe = await recipe_for(db, item_id)
    if recipe is None:
        raise RunRefused(RunError.RUN_NO_RECIPE)
    took = await planned(db, recipe, batches, use)
    held = await summary.stock(db, [line.item_id for line in took.lines], today)
    can_use = [held[line.item_id].get(location_id, summary.Stock()).usable for line in took.lines]
    lines = [
        PlanLine(line.item_id, quantity, usable, short(quantity, usable))
        for line, quantity, usable in zip(took.lines, took.quantities, can_use, strict=True)
    ]
    most = units.covered([line.quantity for line in took.lines], can_use)
    most_use = None
    if use is not None and most is not None and any(line.short for line in lines):
        named = next(line for line in took.lines if line.item_id == use[0])
        most_use = units.scaled(named.quantity, most, down=True)
    return Plan(
        batches=took.batches,
        makes=took.makes,
        lines=lines,
        most=None if most is None else units.most_batches(most),
        most_use=most_use,
    )


def not_completed(run: ProductionRun) -> None:
    """Refuse a change to what a completed run moved."""
    if run.status is RunStatus.COMPLETED:
        raise RunRefused(RunError.RUN_COMPLETED)


async def plan_batches(db: WriteSession, run: ProductionRun, batches: Decimal) -> None:
    """Plan ``batches`` for a run not yet completed, scaling its lines."""
    not_completed(run)
    for line in await lines(db, run.id):
        line.quantity = units.scaled(line.quantity, batches / run.planned_batches)
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
        if await holds.refusal(db, [(lot.item_id, lot.id)]):
            raise RunRefused(HoldError.LOT_ON_HOLD)
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


async def _make_bases(db: WriteSession, run: ProductionRun, made_on: date) -> None:
    """Make first the bases ``run``'s lines take, naming no lot, that are set
    to auto-manufacture: all a line takes, or what it takes beyond what is
    usable at the run's location. One that can't be made goes short."""
    taking: dict[int, Decimal] = defaultdict(Decimal)
    for line in await lines(db, run.id):
        if line.lot_id is None:
            taking[line.item_id] += line.quantity
    found = await db.exec(
        select(Base)
        .where(
            col(Base.id).in_(list(taking)),
            col(Base.auto_manufacture).in_([AutoManufacture.ALWAYS, AutoManufacture.OUT_OF_STOCK]),
        )
        .order_by(col(Base.id))
    )
    for base in found.all():
        wanted = taking[base.id]
        if base.auto_manufacture is AutoManufacture.OUT_OF_STOCK:
            held = (await summary.stock(db, [base.id], made_on))[base.id]
            usable = held.get(run.location_id, summary.Stock()).usable
            wanted = short(wanted, max(usable, Decimal(0))) or Decimal(0)
        if wanted <= 0:
            continue
        # Refused only as it plans, before it writes anything: the line goes short.
        with suppress(RunRefused):
            await make(
                db, base.id, run.location_id, wanted, made_on, initiative_id=run.initiative_id
            )


async def complete(
    db: WriteSession,
    run: ProductionRun,
    actual_quantity: Decimal,
    completed_on: date,
    *,
    quiet: bool = False,
) -> None:
    """Complete ``run``, having made ``actual_quantity`` on ``completed_on``:
    the bases it uses that are set to be made, made first, its lines out,
    from their lots, and what it made into its output lot, on hold while it
    cures. Refused while a line names a lot on hold, or names none of an item
    whose every lot is recorded. A ``quiet`` run records no event, and its
    lines need name no lot."""
    not_completed(run)
    draws = [(line.item_id, line.lot_id) for line in await lines(db, run.id)]
    refused = await holds.refusal(db, draws, named=not quiet)
    if refused is not None:
        raise RunRefused(refused)
    await _make_bases(db, run, completed_on)
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
    await holds.cure(db, run, quiet=quiet)


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
    batches = units.scale(recipe.batch_quantity, quantity).quantize(PLACES)
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
