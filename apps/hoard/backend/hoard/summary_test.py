"""Item figures: stock, potential and could make, made, last stocktake and
costs, each read once for many items."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import purchases, recipes, runs, summary
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    CategoryKind,
    Location,
    Product,
    ProductionRun,
    Purchase,
    PurchaseLine,
    Recipe,
    Stocktake,
    Unit,
)
from hoard.stock import add_product, adjust
from hoard.stocktakes import confirm_row, start_stocktake
from seneschal_core.db import WriteSession

TODAY = date(2026, 11, 15)


async def _location(db: WriteSession, name: str) -> Location:
    location = Location(organization_id=db.organization_id, name=name)
    db.add(location)
    await db.flush()
    return location


async def _alyx(db: WriteSession, unit, base, material):
    """Alyx batch: 2 catnip to 1 chamomile, a batch of 300 g."""
    catnip, chamomile = await material("Catnip"), await material("Chamomile")
    alyx = await base("Alyx batch")
    await recipes.save(
        db,
        Recipe(base_id=alyx.id, batch_quantity=Decimal(300)),
        [recipes.Line(catnip.id, Decimal(200)), recipes.Line(chamomile.id, Decimal(100))],
    )
    return catnip, chamomile, alyx


async def test_potential_adds_up_each_locations_could_make(
    db: WriteSession, main: Location, unit, base, material, lot
):
    shed = await _location(db, "Shed")
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    await lot(catnip.id, "C1", "1000")
    await lot(chamomile.id, "H1", "1000", location=shed)

    [figures] = (await summary.summarise(db, [alyx.id], TODAY)).values()

    # Catnip in one place and chamomile in another make nothing.
    assert figures.potential == 0
    assert figures.locations == {}

    await lot(catnip.id, "C2", "400", location=shed)
    await lot(chamomile.id, "H2", "150")

    [figures] = (await summary.summarise(db, [alyx.id], TODAY)).values()

    # Main: 1000 catnip and 150 chamomile cover 1.5 batches; the shed's 400
    # catnip and 1000 chamomile cover 2.
    assert {at: figures.locations[at].could_make for at in figures.locations} == {
        main.id: Decimal(450),
        shed.id: Decimal(600),
    }
    assert figures.potential == Decimal(1050)


async def test_potential_leaves_out_committed_held_and_expired_stock(
    db: WriteSession, main: Location, unit, base, material, lot, hold, sell
):
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    await lot(catnip.id, "C1", "1000")
    await lot(chamomile.id, "H1", "200")
    await hold(await lot(chamomile.id, "H2", "500"))
    await lot(chamomile.id, "OLD", "500", expires_on=date(2026, 1, 1))
    await sell((catnip.id, "600", "1"))

    figures = await summary.summarise(db, [alyx.id, catnip.id, chamomile.id], TODAY)
    held = figures[chamomile.id].locations[main.id].stock

    assert (held.on_hand, held.on_hold, held.expired, held.usable) == (1200, 500, 500, 200)
    assert figures[catnip.id].stock.available == 400
    # 400 catnip covers 2 batches, 200 chamomile 2: what open orders commit,
    # held lots and expired ones count for nothing.
    assert figures[alyx.id].potential == Decimal(600)


async def test_potential_is_what_make_as_much_as_makes_at_most(
    db: WriteSession, main: Location, unit, base, material, lot, sell
):
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    await lot(catnip.id, "C1", "777")
    await lot(chamomile.id, "H1", "1000", expires_on=date(2026, 1, 1))
    await lot(chamomile.id, "H2", "350")
    await sell((catnip.id, "100", "1"))

    plan = await runs.plan(db, alyx.id, main.id, Decimal(1), None, TODAY)
    [figures] = (await summary.summarise(db, [alyx.id], TODAY)).values()

    assert plan.most == Decimal("3.385")
    assert figures.potential == Decimal(300) * plan.most == Decimal("1015.5")


async def test_no_recipe_or_no_lines_is_no_potential_and_a_variation_has_one(
    db: WriteSession, main: Location, unit, base, material, lot
):
    plain, empty = await base("Plain"), await base("Empty")
    await recipes.save(db, Recipe(base_id=empty.id), [])
    tin = await material("Tin")
    await lot(tin.id, "T1", "5")
    _, candle = await add_product(db, Product(name="Candle"))
    await recipes.save(db, Recipe(variation_id=candle.id), [recipes.Line(tin.id, Decimal(1))])

    figures = await summary.summarise(db, [plain.id, empty.id, candle.id], TODAY)

    assert (figures[plain.id].potential, figures[empty.id].potential) == (None, None)
    assert figures[candle.id].potential == 5
    assert figures[candle.id].locations[main.id].could_make == 5


async def test_a_base_made_of_a_base_counts_what_is_on_hand_of_it(
    db: WriteSession, main: Location, unit, base, material, lot
):
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    await lot(catnip.id, "C1", "1000")
    await lot(chamomile.id, "H1", "1000")
    blend = await base("Blend")
    await recipes.save(
        db, Recipe(base_id=blend.id, batch_quantity=Decimal(1)), [recipes.Line(alyx.id, Decimal(1))]
    )

    figures = await summary.summarise(db, [alyx.id, blend.id], TODAY)

    assert (figures[alyx.id].potential, figures[blend.id].potential) == (1500, 0)

    await lot(alyx.id, "A1", "40")

    assert (await summary.summarise(db, [blend.id], TODAY))[blend.id].potential == 40


async def test_made_counts_completed_runs_until_one_is_reopened(
    db: WriteSession, main: Location, unit, base, material, lot
):
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    await lot(catnip.id, "C1", "10000")
    await lot(chamomile.id, "H1", "10000")
    made = await runs.make(db, alyx.id, main.id, Decimal(300), TODAY)
    opened = await runs.open_run(
        db, ProductionRun(code="R-X", item_id=alyx.id, location_id=main.id)
    )
    await runs.complete(db, opened, Decimal(290), TODAY)
    await runs.open_run(db, ProductionRun(code="R-Y", item_id=alyx.id, location_id=main.id))

    assert made is not None
    assert (await summary.summarise(db, [alyx.id]))[alyx.id].made == 590

    await runs.reopen(db, opened)

    assert (await summary.summarise(db, [alyx.id]))[alyx.id].made == 300


async def test_the_last_stocktake_is_the_latest_that_counted_it(
    db: WriteSession, main: Location, material, lot
):
    shed = await _location(db, "Shed")
    catnip = await material("Catnip")
    await lot(catnip.id, "C1", "10")

    async def count(location: Location, dated_on: date, counted: str | None) -> None:
        stocktake = Stocktake(
            kind=CategoryKind.MATERIAL, location_id=location.id, dated_on=dated_on
        )
        [row] = await start_stocktake(db, stocktake)
        if counted is not None:
            await confirm_row(db, row, Decimal(counted))

    await count(main, date(2026, 11, 1), "10")
    await count(shed, date(2026, 11, 3), "0")
    # A stocktake that left its row uncounted didn't count it.
    await count(main, date(2026, 11, 9), None)

    [figures] = (await summary.summarise(db, [catnip.id], TODAY)).values()

    assert figures.last_stocktake == date(2026, 11, 3)
    assert {at: figures.locations[at].last_stocktake for at in figures.locations} == {
        main.id: date(2026, 11, 1),
        shed.id: date(2026, 11, 3),
    }


async def test_an_item_out_of_stock_keeps_its_last_known_cost(
    db: WriteSession, main: Location, material, buy
):
    rose_hip, lychee = await material("Rose hip"), await material("Lychee")
    await buy((rose_hip, "10", "6.81"))
    await buy((lychee, "10", "0"))

    assert await summary.costs(db, [rose_hip.id, lychee.id]) == {
        rose_hip.id: summary.Cost(Decimal("6.81"), last_known=False)
    }

    await adjust(
        db,
        Adjustment(
            item_id=rose_hip.id,
            location_id=main.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 2),
            change=Decimal(-10),
        ),
    )
    # Found again by hand at no cost: its average is nothing, so it is still
    # costed at what it last cost.
    await adjust(
        db,
        Adjustment(
            item_id=rose_hip.id,
            location_id=main.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 3),
            change=Decimal(2),
        ),
    )

    assert await summary.costs(db, [rose_hip.id, lychee.id]) == {
        rose_hip.id: summary.Cost(Decimal("6.81"), last_known=True)
    }


def test_a_recipe_costs_its_lines_and_labour_and_says_when_a_cost_is_missing():
    costed = summary.recipe_cost(
        [1, 2, 3],
        [Decimal(600), Decimal(300), Decimal(30)],
        {
            1: summary.Cost(Decimal("0.02"), last_known=False),
            2: summary.Cost(Decimal("0.01"), last_known=True),
        },
        makes=Decimal(931),
        batches=Decimal(1),
        minutes=30,
        rate=Decimal(20),
    )

    assert costed.lines == [Decimal(12), Decimal(3), None]
    assert costed.last_known == [False, True, False]
    assert (costed.labour, costed.total, costed.incomplete) == (Decimal(10), Decimal(25), True)
    assert costed.unit_cost == Decimal("0.026853")

    doubled = summary.recipe_cost(
        [1],
        [Decimal(1200)],
        {1: summary.Cost(Decimal("0.02"), last_known=False)},
        makes=Decimal(1862),
        batches=Decimal(2),
        minutes=30,
        rate=None,
    )

    assert (doubled.labour, doubled.total, doubled.incomplete) == (None, Decimal(24), False)


async def test_on_order_is_what_is_still_to_come_and_says_when_it_is_overdue(
    db: WriteSession, main: Location, material
):
    chamomile = await material("Chamomile")
    purchase = Purchase(
        organization_id=db.organization_id,
        code="P-1",
        dated_on=date(2026, 11, 8),
        expected_on=date(2026, 11, 14),
        location_id=main.id,
    )
    db.add(purchase)
    await db.flush()
    line = PurchaseLine(
        organization_id=db.organization_id,
        purchase_id=purchase.id,
        item_id=chamomile.id,
        purchase_quantity=Decimal(1),
        tracking_quantity=Decimal("453.592"),
        unit_cost=Decimal("9.07"),
    )
    db.add(line)
    await db.flush()

    await purchases.receive_line(db, line, received_on=date(2026, 11, 10), quantity=Decimal(200))
    [figures] = (await summary.summarise(db, [chamomile.id], TODAY)).values()

    # Only the part still to come, from a purchase past the day it was due.
    assert figures.on_order == summary.OnOrder(Decimal("253.592"), overdue=True)
    purchase.expected_on = TODAY
    [figures] = (await summary.summarise(db, [chamomile.id], TODAY)).values()
    assert figures.on_order == summary.OnOrder(Decimal("253.592"), overdue=False)


def test_a_window_ends_today_and_starts_as_many_months_back():
    starts = {window: summary.window_start(window, TODAY) for window in summary.UsageWindow}
    assert starts == {
        summary.UsageWindow.WEEK: date(2026, 11, 9),
        summary.UsageWindow.MONTH: date(2026, 10, 16),
        summary.UsageWindow.MONTHS_3: date(2026, 8, 16),
        summary.UsageWindow.MONTHS_6: date(2026, 5, 16),
        summary.UsageWindow.MONTHS_12: date(2025, 11, 16),
        summary.UsageWindow.ALL: None,
    }
    # A month with no such day starts after its last.
    assert summary.window_start(summary.UsageWindow.MONTH, date(2026, 3, 31)) == date(2026, 3, 1)


async def test_usage_counts_what_purchases_received_and_runs_used_by_their_day(
    db: WriteSession, main: Location, unit, material, base, buy
):
    chamomile = await material("Chamomile")
    tea = await base("Chamomile blend")
    await recipes.save(db, Recipe(base_id=tea.id), [recipes.Line(chamomile.id, Decimal(1))])
    await buy((chamomile, "30", "1"), dated_on=date(2025, 6, 1))
    await buy((chamomile, "50", "1"), dated_on=date(2026, 10, 1))
    await buy((chamomile, "100", "1"), dated_on=date(2026, 11, 14))
    # Received after today: in no window yet.
    await buy((chamomile, "7", "1"), dated_on=date(2026, 11, 20))
    await runs.make(db, tea.id, main.id, Decimal(10), date(2026, 8, 1))
    await runs.make(db, tea.id, main.id, Decimal(20), date(2026, 11, 12))
    # A loss counts as neither.
    await adjust(
        db,
        Adjustment(
            item_id=chamomile.id,
            location_id=main.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=date(2026, 11, 13),
            change=Decimal(-5),
        ),
    )

    figures = await summary.summarise(db, [chamomile.id, tea.id], TODAY)

    assert [
        (usage.window, usage.received, usage.used) for usage in figures[chamomile.id].usage
    ] == [
        (summary.UsageWindow.WEEK, 100, 20),
        (summary.UsageWindow.MONTH, 100, 20),
        (summary.UsageWindow.MONTHS_3, 150, 20),
        (summary.UsageWindow.MONTHS_6, 150, 30),
        (summary.UsageWindow.MONTHS_12, 150, 30),
        (summary.UsageWindow.ALL, 180, 30),
    ]
    # What a run made is not used: the blend has received and used nothing.
    assert {(usage.received, usage.used) for usage in figures[tea.id].usage} == {(0, 0)}


async def test_used_in_lists_each_recipe_holding_it_but_bundles(
    db: WriteSession, unit, base, material
):
    catnip, chamomile, alyx = await _alyx(db, unit, base, material)
    kilograms = Unit(organization_id=db.organization_id, name="kg", standard="kilogram")
    db.add(kilograms)
    await db.flush()
    _, tin = await add_product(db, Product(name="Chamomile tin"))
    await recipes.save(
        db,
        Recipe(variation_id=tin.id),
        [recipes.Line(chamomile.id, Decimal("0.05"), kilograms.id)],
    )
    _, gift = await add_product(db, Product(name="Gift box"))
    await recipes.save(
        db,
        Recipe(variation_id=gift.id, batch_quantity=Decimal(2)),
        [recipes.Line(tin.id, Decimal(2))],
    )
    pair, _ = await add_product(db, Product(name="Two tins"))
    await recipes.save(db, Recipe(product_id=pair.id), [recipes.Line(tin.id, Decimal(2))])

    figures = await summary.summarise(db, [chamomile.id, tin.id, catnip.id], TODAY)

    assert [
        (used.makes, used.batch_quantity, used.amount, used.unit_id)
        for used in figures[chamomile.id].used_in
    ] == [
        (alyx.id, Decimal(300), Decimal(100), unit.id),
        (tin.id, Decimal(1), Decimal("0.05"), kilograms.id),
    ]
    # A product is used in another's recipe, counted in each; the bundle
    # holding it makes nothing.
    assert [(used.makes, used.amount, used.unit_id) for used in figures[tin.id].used_in] == [
        (gift.id, Decimal(2), None)
    ]
