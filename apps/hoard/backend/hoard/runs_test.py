"""Production runs: lines from a recipe, assumed lots, completing and reopening,
what a run costs, and the events it records."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlmodel import col, select

from hoard import purchases, runs
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Item,
    Location,
    Lot,
    OutboxEvent,
    Product,
    ProductionRun,
    ProductionRunLine,
    PurchaseLine,
    Recipe,
    RecipeLine,
    RunStatus,
)
from hoard.stock import StockError, add_product, adjust, on_hand
from seneschal_core.db import WriteSession


async def _recipe(
    db: WriteSession, *lines: tuple[Any, str], batch: str = "1", **output: int
) -> Recipe:
    recipe = Recipe(organization_id=db.organization_id, batch_quantity=Decimal(batch), **output)
    db.add(recipe)
    await db.flush()
    for position, (item, quantity) in enumerate(lines):
        db.add(
            RecipeLine(
                organization_id=db.organization_id,
                recipe_id=recipe.id,
                item_id=item.id,
                quantity=Decimal(quantity),
                position=position,
            )
        )
    await db.flush()
    return recipe


async def _open(
    db: WriteSession, item_id: int, location: Location, batches: str = "1", **fields
) -> ProductionRun:
    run = ProductionRun(
        code=await runs.next_code(db),
        item_id=item_id,
        location_id=location.id,
        planned_batches=Decimal(batches),
        **fields,
    )
    return await runs.open_run(db, run)


async def _complete(db: WriteSession, run: ProductionRun, made: str, day: int) -> ProductionRun:
    await runs.complete(db, run, Decimal(made), date(2026, 11, day))
    await db.refresh(run)
    return run


async def _average(db: WriteSession, item_id: int) -> Decimal:
    return (
        await db.exec(
            select(Item.average_cost)
            .where(Item.id == item_id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def _lot(
    db: WriteSession,
    item_id: int,
    location: Location,
    number: str,
    quantity: str,
    dated_on: date,
    expires_on: date | None = None,
) -> Lot:
    """A lot that starts with ``quantity``, at a unit cost of one."""
    lot = Lot(
        organization_id=db.organization_id,
        item_id=item_id,
        location_id=location.id,
        number=number,
        dated_on=dated_on,
        expires_on=expires_on,
    )
    db.add(lot)
    await db.flush()
    await adjust(
        db,
        Adjustment(
            item_id=item_id,
            location_id=location.id,
            lot_id=lot.id,
            kind=AdjustmentKind.STARTING,
            dated_on=dated_on,
            change=Decimal(quantity),
            unit_cost=Decimal(1),
        ),
    )
    return lot


async def _lines(
    db: WriteSession, run: ProductionRun
) -> list[tuple[int, Decimal, int | None, bool]]:
    return [
        (line.item_id, line.quantity, line.lot_id, line.assumed)
        for line in await runs.lines(db, run.id)
    ]


async def test_completing_a_run_moves_stock_into_its_output_lot_at_its_cost(
    db: WriteSession, main: Location, material, base, buy
):
    flour, sugar = await material("Flour"), await material("Sugar")
    await buy((flour, "10", "2"), (sugar, "5", "4"))
    dough = await base("Dough")
    await _recipe(db, (flour, "2"), (sugar, "1"), batch="3", base_id=dough.id)

    run = await _open(db, dough.id, main, batches="2", expires_on=date(2026, 12, 1))
    assert [(item, quantity) for item, quantity, _, _ in await _lines(db, run)] == [
        (flour.id, 4),
        (sugar.id, 2),
    ]
    # Opening a run moves no stock.
    assert await on_hand(db, flour.id) == 10

    run = await _complete(db, run, "5", 3)
    assert (run.status, run.actual_quantity, run.completed_on, run.started_on) == (
        RunStatus.COMPLETED,
        5,
        date(2026, 11, 3),
        date(2026, 11, 3),
    )
    assert (await on_hand(db, flour.id), await on_hand(db, sugar.id)) == (6, 3)
    assert await on_hand(db, dough.id) == 5
    lot = await db.get_one(Lot, run.output_lot_id)
    await db.refresh(lot)
    assert (lot.item_id, lot.number, lot.balance, lot.expires_on) == (
        dough.id,
        run.code,
        5,
        date(2026, 12, 1),
    )
    # What it consumed, 4 at 2 and 2 at 4, over the 5 it made.
    assert await _average(db, dough.id) == Decimal("3.2")
    assert not run.uncosted
    # Each line drew on its purchase's lot, assumed.
    assert all(lot_id is not None and assumed for *_, lot_id, assumed in await _lines(db, run))

    with pytest.raises(runs.RunRefused) as refused:
        await runs.complete(db, run, Decimal(1), date(2026, 11, 4))
    assert refused.value.code is runs.RunError.RUN_COMPLETED


async def test_a_line_assumes_the_earliest_expiry_then_the_first_in(
    db: WriteSession, main: Location, material, base
):
    flour, salt = await material("Flour"), await material("Salt")
    late = await _lot(db, flour.id, main, "A", "3", date(2026, 11, 1), date(2026, 12, 10))
    soon = await _lot(db, flour.id, main, "B", "2", date(2026, 11, 2), date(2026, 12, 5))
    lasting = await _lot(db, flour.id, main, "C", "10", date(2026, 10, 1))
    first_salt = await _lot(db, salt.id, main, "S1", "5", date(2026, 10, 1))
    chosen_salt = await _lot(db, salt.id, main, "S2", "5", date(2026, 11, 1))
    dough = await base("Dough")
    await _recipe(db, (flour, "6"), (salt, "1"), base_id=dough.id)

    run = await _open(db, dough.id, main)
    salt_line = (await runs.lines(db, run.id))[1]
    await runs.record_lot(db, run, salt_line, chosen_salt.id)
    await _complete(db, run, "6", 3)

    assert sorted(await _lines(db, run), key=lambda line: (line[0], line[2] or 0)) == sorted(
        [
            (flour.id, 2, soon.id, True),
            (flour.id, 3, late.id, True),
            (flour.id, 1, lasting.id, True),
            (salt.id, 1, chosen_salt.id, False),
        ],
        key=lambda line: (line[0], line[2] or 0),
    )
    await db.refresh(first_salt)
    assert first_salt.balance == 5

    # A lot of another item is refused.
    other = await _open(db, dough.id, main)
    with pytest.raises(runs.RunRefused) as refused:
        await runs.record_lot(db, other, (await runs.lines(db, other.id))[0], first_salt.id)
    assert refused.value.code is runs.RunError.RUN_LOT_MISMATCH


async def test_more_than_the_lots_hold_draws_the_rest_on_no_lot(
    db: WriteSession, main: Location, material, base
):
    flour = await material("Flour")
    held = await _lot(db, flour.id, main, "A", "2", date(2026, 11, 1))
    dough = await base("Dough")
    await _recipe(db, (flour, "5"), base_id=dough.id)

    run = await _complete(db, await _open(db, dough.id, main), "5", 3)
    assert await _lines(db, run) == [(flour.id, 2, held.id, True), (flour.id, 3, None, False)]
    assert await on_hand(db, flour.id) == -3


async def test_reopening_a_run_takes_back_what_it_moved(
    db: WriteSession, main: Location, material, base
):
    flour = await material("Flour")
    await _lot(db, flour.id, main, "A", "2", date(2026, 11, 1))
    await _lot(db, flour.id, main, "B", "8", date(2026, 11, 2))
    dough = await base("Dough")
    await _recipe(db, (flour, "4"), base_id=dough.id)
    run = await _complete(db, await _open(db, dough.id, main), "4", 3)
    lot_id = run.output_lot_id

    await runs.reopen(db, run)
    await db.refresh(run)
    assert (run.status, run.actual_quantity, run.completed_on, run.output_lot_id) == (
        RunStatus.IN_PROGRESS,
        None,
        None,
        None,
    )
    # The assumed lines are one line again, naming no lot.
    assert await _lines(db, run) == [(flour.id, 4, None, False)]
    assert (await on_hand(db, flour.id), await on_hand(db, dough.id)) == (10, 0)
    assert await db.get(Lot, lot_id) is None
    made = await db.exec(select(Adjustment).where(Adjustment.production_run_id == run.id))
    assert made.all() == []
    assert await _average(db, dough.id) == 0

    with pytest.raises(runs.RunRefused) as refused:
        await runs.reopen(db, run)
    assert refused.value.code is runs.RunError.RUN_NOT_COMPLETED

    # Once something else draws on its output lot, it can't be reopened.
    run = await _complete(db, run, "4", 4)
    await adjust(
        db,
        Adjustment(
            item_id=dough.id,
            location_id=main.id,
            lot_id=run.output_lot_id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 5),
            change=Decimal(-1),
        ),
    )
    with pytest.raises(runs.RunRefused) as refused:
        await runs.reopen(db, run)
    assert refused.value.code is StockError.LOT_IN_USE


async def test_a_purchase_corrected_flows_through_runs_to_what_they_made(
    db: WriteSession, main: Location, material, base, buy
):
    flour = await material("Flour")
    purchase = await buy((flour, "10", "2"), dated_on=date(2026, 11, 1))
    dough = await base("Dough")
    await _recipe(db, (flour, "4"), base_id=dough.id)
    product, bread = await add_product(db, Product(name="Bread"))
    await _recipe(db, (dough, "2"), variation_id=bread.id)

    await _complete(db, await _open(db, dough.id, main), "4", 3)
    await _complete(db, await _open(db, bread.id, main), "1", 4)
    assert (await _average(db, dough.id), await _average(db, bread.id)) == (2, 4)

    line = (
        await db.exec(select(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
    ).one()
    line.unit_cost = Decimal(3)
    await purchases.rederive_purchase(db, purchase)
    assert await _average(db, flour.id) == 3
    # Flour at 3: dough costs 12 over 4, and bread two of those.
    assert (await _average(db, dough.id), await _average(db, bread.id)) == (3, 6)


async def test_a_run_consuming_what_cost_nothing_is_uncosted_until_it_does(
    db: WriteSession, main: Location, material, base, buy
):
    sugar = await material("Sugar")
    syrup = await base("Syrup")
    await _recipe(db, (sugar, "2"), base_id=syrup.id)

    run = await _complete(db, await _open(db, syrup.id, main), "2", 5)
    assert run.uncosted
    assert await _average(db, syrup.id) == 0

    # Sugar bought before the run's date costs it.
    await buy((sugar, "4", "5"), dated_on=date(2026, 11, 1))
    await db.refresh(run)
    assert not run.uncosted
    assert await _average(db, syrup.id) == 5


async def test_runs_record_their_events_and_low_stock(
    db: WriteSession, main: Location, material, base, buy
):
    flour = await material("Flour", low_stock_limit=Decimal(7))
    await buy((flour, "10", "2"))
    dough = await base("Dough")
    await _recipe(db, (flour, "4"), base_id=dough.id)

    run = await _open(db, dough.id, main, initiative_id=12)
    later = await _open(db, dough.id, main, status=RunStatus.IN_PROGRESS)
    await runs.start(db, run, date(2026, 11, 2))
    with pytest.raises(runs.RunRefused):
        await runs.start(db, run, date(2026, 11, 2))
    await _complete(db, run, "4", 3)

    recorded = await db.exec(
        select(OutboxEvent)
        .where(col(OutboxEvent.name).in_(["run-started", "run-completed", "stock-low"]))
        .order_by(col(OutboxEvent.id))
    )
    assert [
        (event.name, event.payload.get("run_id"), event.initiative_id) for event in recorded
    ] == [
        ("run-started", later.id, None),
        ("run-started", run.id, 12),
        ("stock-low", None, None),
        ("run-completed", run.id, 12),
    ]


async def test_planning_batches_scales_the_lines_and_a_run_needs_a_recipe(
    db: WriteSession, main: Location, material, base
):
    flour = await material("Flour")
    dough = await base("Dough")
    await _recipe(db, (flour, "3"), base_id=dough.id)
    run = await _open(db, dough.id, main, batches="2")

    await runs.plan_batches(db, run, Decimal(3))
    assert await _lines(db, run) == [(flour.id, 9, None, False)]

    with pytest.raises(runs.RunRefused) as refused:
        await _open(db, flour.id, main)
    assert refused.value.code is runs.RunError.RUN_NO_RECIPE

    await runs.delete_run(db, run)
    assert (await db.exec(select(ProductionRunLine))).all() == []


async def test_a_run_made_at_once_moves_stock_and_records_no_events(
    db: WriteSession, main: Location, material, base, buy
):
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    dough = await base("Dough")
    await _recipe(db, (flour, "2"), batch="4", base_id=dough.id)

    run = await runs.make(db, dough.id, main.id, Decimal(6), date(2026, 11, 5), initiative_id=3)

    assert run is not None
    assert (run.status, run.planned_batches, run.actual_quantity, run.initiative_id) == (
        RunStatus.COMPLETED,
        Decimal("1.5"),
        6,
        3,
    )
    assert (await on_hand(db, flour.id), await on_hand(db, dough.id)) == (7, 6)
    recorded = await db.exec(select(OutboxEvent).where(col(OutboxEvent.name).like("run-%")))
    assert recorded.all() == []
    # Without a recipe, nothing is made.
    assert await runs.make(db, flour.id, main.id, Decimal(1), date(2026, 11, 5)) is None
