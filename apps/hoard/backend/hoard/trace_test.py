"""Tracing a lot back to its suppliers and forward to its customers, recorded
and assumed, and recalls from a lot or an item's lots in a date range."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlmodel import col, select

from hoard import runs, shipments, trace
from hoard.records import (
    Category,
    CategoryKind,
    Location,
    Lot,
    OrderLine,
    Picking,
    Product,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    PurchaseLine,
    Recipe,
    RecipeLine,
    Shipment,
)
from hoard.shipments import Pick
from hoard.stock import add_product
from hoard.trace import StepKind
from seneschal_core.db import WriteSession


async def _recipe(
    db: WriteSession,
    *lines: tuple[int, str],
    base_id: int | None = None,
    variation_id: int | None = None,
) -> None:
    recipe = Recipe(organization_id=db.organization_id, base_id=base_id, variation_id=variation_id)
    db.add(recipe)
    await db.flush()
    for position, (line_item, quantity) in enumerate(lines):
        db.add(
            RecipeLine(
                organization_id=db.organization_id,
                recipe_id=recipe.id,
                item_id=line_item,
                quantity=Decimal(quantity),
                position=position,
            )
        )
    await db.flush()


async def _run(
    db: WriteSession, item_id: int, main: Location, day: int, *, made: str, lot: Lot | None
) -> ProductionRun:
    """Complete a run of ``item_id`` on a day of November, recording that its
    one line draws on ``lot``, or leaving the lot to be assumed."""
    run = ProductionRun(code=await runs.next_code(db), item_id=item_id, location_id=main.id)
    await runs.open_run(db, run)
    if lot is not None:
        [line] = await runs.lines(db, run.id)
        await runs.record_lot(db, run, line, lot.id)
    await runs.complete(db, run, Decimal(made), date(2026, 11, day))
    await db.refresh(run)
    return run


async def _bought_lot(db: WriteSession, purchase: Purchase) -> Lot:
    return (
        await db.exec(
            select(Lot)
            .join(PurchaseLine, col(PurchaseLine.id) == Lot.purchase_line_id)
            .where(PurchaseLine.purchase_id == purchase.id)
        )
    ).one()


@dataclass
class Chai:
    """Cinnamon bought, made into a spice base, made into chai, shipped."""

    purchase: Purchase
    cinnamon: Lot
    spice_run: ProductionRun
    spice: Lot
    chai_run: ProductionRun
    chai: Lot
    chai_item: int
    product: Product


@pytest.fixture
def chai(
    db: WriteSession,
    main: Location,
    material: Callable[..., Awaitable[Any]],
    base: Callable[..., Awaitable[Any]],
    buy: Callable[..., Awaitable[Purchase]],
) -> Callable[..., Awaitable[Chai]]:
    async def make(*, assume_spice: bool = False) -> Chai:
        cinnamon = await material("Cinnamon")
        spice = await base("Chai spice")
        product, chai = await add_product(db, Product(name="Chai"))
        await _recipe(db, (cinnamon.id, "2"), base_id=spice.id)
        await _recipe(db, (spice.id, "1"), variation_id=chai.id)
        purchase = await buy((cinnamon, "10", "2"), supplier_contact_id=7)
        cinnamon_lot = await _bought_lot(db, purchase)
        spice_run = await _run(db, spice.id, main, 3, made="4", lot=cinnamon_lot)
        spice_lot = await db.get_one(Lot, spice_run.output_lot_id)
        chai_run = await _run(
            db, chai.id, main, 4, made="4", lot=None if assume_spice else spice_lot
        )
        chai_lot = await db.get_one(Lot, chai_run.output_lot_id)
        return Chai(
            purchase, cinnamon_lot, spice_run, spice_lot, chai_run, chai_lot, chai.id, product
        )

    return make


@pytest.fixture
def ship(
    db: WriteSession, main: Location, sell: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Shipment]]:
    """Ship ``quantity`` of an item to a customer on a day of November, from
    ``lot`` or from whichever is assumed; by hand, or as a store's fulfillment,
    which posts what it sent without naming a lot."""

    async def ship(
        item_id: int,
        quantity: str,
        day: int,
        *,
        lot: Lot | None = None,
        customer: int = 9,
        store: bool = False,
    ) -> Shipment:
        order = await sell((item_id, quantity, "5"), customer_contact_id=customer)
        line_id = (await db.exec(select(OrderLine.id).where(OrderLine.order_id == order.id))).one()
        shipment = await shipments.add(
            db, order, main.id, [Pick(line_id, lot_id=None if lot is None else lot.id)]
        )
        if store:
            await shipments.post(db, shipment, date(2026, 11, day))
        else:
            await shipments.ship(db, shipment, date(2026, 11, day))
        return shipment

    return ship


def _steps(found: trace.Trace) -> list[tuple[Any, ...]]:
    return [
        (step.level, step.kind, step.lot_id, step.made_lot_id, step.contact_id, step.assumed)
        for step in found.steps
    ]


async def test_forward_follows_what_was_made_from_a_lot_to_who_it_was_shipped_to(
    db: WriteSession, chai, ship
):
    made = await chai()
    shipment = await ship(made.chai_item, "3", 5, lot=made.chai)

    found = await trace.forward(db, made.cinnamon.id)

    assert _steps(found) == [
        (1, StepKind.RUN, made.cinnamon.id, made.spice.id, None, False),
        (2, StepKind.RUN, made.spice.id, made.chai.id, None, False),
        (3, StepKind.SHIPMENT, made.chai.id, None, 9, False),
    ]
    shipped = found.steps[-1]
    assert (shipped.record_id, shipped.quantity, shipped.dated_on) == (
        shipment.id,
        Decimal(3),
        date(2026, 11, 5),
    )
    assert found.lot_ids(assumed=False) == {made.spice.id, made.chai.id}


async def test_what_follows_an_assumed_lot_is_assumed(db: WriteSession, chai, ship):
    made = await chai(assume_spice=True)
    await ship(made.chai_item, "3", 5, lot=made.chai)

    found = await trace.forward(db, made.spice.id)

    assert _steps(found) == [
        (1, StepKind.RUN, made.spice.id, made.chai.id, None, True),
        (2, StepKind.SHIPMENT, made.chai.id, None, 9, True),
    ]
    assert found.lot_ids(assumed=False) == set()
    assert found.lot_ids() == {made.chai.id}


async def test_draws_naming_no_lot_count_while_the_lot_held_stock(db: WriteSession, chai, ship):
    made = await chai()
    recorded = Category(
        organization_id=db.organization_id,
        kind=CategoryKind.PRODUCT,
        name="Drinks",
        picking=Picking.RECORDED,
    )
    db.add(recorded)
    await db.flush()
    made.product.category_id = recorded.id
    await db.flush()
    # A store's fulfillment names no lot even where the category records every one.
    while_held = await ship(made.chai_item, "1", 4, customer=11, store=True)
    await ship(made.chai_item, "4", 5, lot=made.chai)
    await ship(made.chai_item, "1", 6, customer=12, store=True)

    found = await trace.forward(db, made.chai.id)

    assert _steps(found) == [
        (1, StepKind.SHIPMENT, None, None, 11, True),
        (1, StepKind.SHIPMENT, made.chai.id, None, 9, False),
    ]
    assert found.steps[0].record_id == while_held.id
    assert found.steps[0].from_lot_id == made.chai.id


async def test_back_follows_a_lot_to_the_lots_and_suppliers_that_went_into_it(
    db: WriteSession, chai
):
    made = await chai(assume_spice=True)

    found = await trace.back(db, made.chai.id)

    assert _steps(found) == [
        (1, StepKind.RUN, made.spice.id, made.chai.id, None, True),
        (2, StepKind.RUN, made.cinnamon.id, made.spice.id, None, True),
        (3, StepKind.PURCHASE, made.cinnamon.id, None, 7, True),
    ]
    assert found.steps[-1].record_id == made.purchase.id
    assert found.steps[-1].quantity == Decimal(10)
    assert found.lot_ids() == {made.spice.id, made.cinnamon.id}


async def test_a_run_line_naming_no_lot_is_a_step_back_to_none(db: WriteSession, chai):
    made = await chai()
    line = (
        await db.exec(select(ProductionRunLine).where(ProductionRunLine.run_id == made.chai_run.id))
    ).one()
    line.lot_id = None
    await db.flush()

    found = await trace.back(db, made.chai.id)

    assert _steps(found) == [(1, StepKind.RUN, None, made.chai.id, None, True)]


async def test_a_recall_traces_a_lot_or_each_lot_of_an_item_in_a_date_range(
    db: WriteSession, material, buy
):
    cinnamon = await material("Cinnamon")
    lots = [
        await _bought_lot(db, await buy((cinnamon, "1", "1"), dated_on=date(2026, 11, day)))
        for day in (1, 5, 9)
    ]

    by_lot = await trace.recall(db, lot_id=lots[0].id)
    in_range = await trace.recall(
        db, item_id=cinnamon.id, since=date(2026, 11, 2), until=date(2026, 11, 9)
    )
    every = await trace.recall(db, item_id=cinnamon.id)

    assert [found.lot_id for found in by_lot] == [lots[0].id]
    assert [found.lot_id for found in in_range] == [lots[1].id, lots[2].id]
    assert [found.lot_id for found in every] == [lot.id for lot in lots]
    with pytest.raises(ValueError):
        await trace.recall(db)
