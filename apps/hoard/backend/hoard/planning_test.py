"""Production planning, called as Initiative calls each read: at
``POST /v1/endpoints``. Dates are counted from today, as the reads count them."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx2
from sqlalchemy import update
from sqlmodel import col, select

from hoard import orders, plugin_manifest, runs
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Channel,
    DefaultHoldReason,
    Listing,
    Location,
    MatchedBy,
    OrderLine,
    OrderStatus,
    Product,
    ProductionRun,
    Purchase,
    PurchaseLine,
    Recipe,
    RecipeLine,
    RunStatus,
    Store,
)
from hoard.reports_test import _read
from hoard.stock import add_product, adjust
from seneschal_core.db import WriteSession

TODAY = date.today()


def _days(days: int) -> date:
    return TODAY + timedelta(days)


async def _recipe(
    db: WriteSession,
    *lines: tuple[Any, str],
    batch: str = "1",
    minutes: int | None = None,
    **output: Any,
) -> Recipe:
    recipe = Recipe(
        organization_id=db.organization_id,
        batch_quantity=Decimal(batch),
        minutes=minutes,
        **output,
    )
    db.add(recipe)
    await db.flush()
    for position, (item, quantity) in enumerate(lines):
        db.add(
            RecipeLine(
                organization_id=db.organization_id,
                recipe_id=recipe.id,
                item_id=item.id,
                quantity=Decimal(quantity),
                written=Decimal(quantity),
                position=position,
            )
        )
    await db.flush()
    return recipe


async def _open(
    db: WriteSession, item_id: int, location: Location, batches: str = "1", **fields: Any
) -> ProductionRun:
    run = ProductionRun(
        code=await runs.next_code(db),
        item_id=item_id,
        location_id=location.id,
        planned_batches=Decimal(batches),
        **fields,
    )
    return await runs.open_run(db, run)


async def _refund(db: WriteSession, order_id: int, selling: str) -> None:
    """Have the store say the order's line now sells ``selling``."""
    line = (await db.exec(select(OrderLine).where(OrderLine.order_id == order_id))).one()
    line.current_quantity = Decimal(selling)
    line.refunded_quantity = line.quantity - Decimal(selling)
    await db.flush()


async def test_what_to_make_next_counts_sales_stock_curing_runs_and_bases_inside_bases(
    client,
    initiative_stand_in,
    community_ref,
    db: WriteSession,
    main,
    material,
    base,
    lot,
    hold,
    sell,
):
    oil = await material("Olive oil")
    lye = await material("Lye")
    solution = await base("Lye solution")
    soap_base = await base("Soap base")
    soap, bar = await add_product(db, Product(name="Lavender soap", cure_days=28))
    box, gift = await add_product(db, Product(name="Gift box", bundle=True))
    await _recipe(db, (lye, "30"), batch="100", base_id=solution.id)
    await _recipe(
        db, (oil, "700"), (solution, "300"), batch="1000", minutes=30, base_id=soap_base.id
    )
    await _recipe(db, (soap_base, "1000"), batch="10", minutes=90, variation_id=bar.id)
    await _recipe(db, (bar, "2"), product_id=box.id)
    # Sold over the last 28 days: 24 bars, a line of 10 of which 6 were
    # refunded, and a gift box holding 2. A cancelled order is not counted,
    # nor is one from before the 28 days.
    await sell((bar.id, "24", "8"), status=OrderStatus.SHIPPED, ordered_on=_days(-5))
    refunded = await sell((bar.id, "10", "8"), status=OrderStatus.SHIPPED, ordered_on=_days(-3))
    await _refund(db, refunded.id, "4")
    await sell((bar.id, "50", "8"), status=OrderStatus.CANCELLED, ordered_on=_days(-2))
    await sell((bar.id, "40", "8"), status=OrderStatus.SHIPPED, ordered_on=_days(-40))
    boxed = await sell(status=OrderStatus.OPEN, ordered_on=_days(-1))
    await orders.add_line(db, boxed, gift.id, Decimal(1))
    boxed.status = OrderStatus.SHIPPED
    # 5 bars available, 10 curing, and a planned run making 10 more.
    await lot(bar.id, "B-1", "5")
    await hold(await lot(bar.id, "B-2", "10"), DefaultHoldReason.CURING)
    await _open(db, bar.id, main, deadline_on=_days(10))
    await lot(soap_base.id, "S-1", "500")
    await db.commit()

    made = await _read(client, initiative_stand_in, community_ref, plugin_manifest.MAKE_NEXT)

    # 30 sold over 28 days, over the 14 days plus 28 cure days: 45 expected.
    assert made == {
        "kind": ["product", "base", "base"],
        "item": ["Lavender soap", "Lye solution", "Soap base"],
        "unit": [None, "g", "g"],
        "needed": ["45.000000", "900.000000", "3000.000000"],
        "available": ["5.000000", "0.000000", "500.000000"],
        "curing": ["10.000000", "0.000000", "0.000000"],
        "planned": ["10.000000", "0.000000", "0.000000"],
        "short": ["20.000000", "900.000000", "2500.000000"],
        "batches": [2, 9, 3],
        "hours": ["3.00", "0.00", "1.50"],
    }


async def test_a_year_of_history_scales_the_rate_by_last_years_same_weeks(
    client, initiative_stand_in, community_ref, db: WriteSession, sell
):
    _, brew = await add_product(db, Product(name="Cold brew"))
    await _recipe(db, batch="10", variation_id=brew.id)
    await sell((brew.id, "28", "4"), status=OrderStatus.SHIPPED, ordered_on=_days(-5))
    # Last year, the 14 days ahead sold twice the rate of the 28 days before them.
    await sell((brew.id, "28", "4"), status=OrderStatus.SHIPPED, ordered_on=_days(-364 + 3))
    await db.commit()

    made = await _read(client, initiative_stand_in, community_ref, plugin_manifest.MAKE_NEXT)
    assert (made["needed"], made["batches"]) == (["14.000000"], [2])

    await sell((brew.id, "28", "4"), status=OrderStatus.SHIPPED, ordered_on=_days(-364 - 28))
    await db.commit()

    made = await _read(client, initiative_stand_in, community_ref, plugin_manifest.MAKE_NEXT)
    assert (made["needed"], made["batches"]) == (["28.000000"], [3])


async def test_nothing_to_make_is_not_listed(
    client, initiative_stand_in, community_ref, db: WriteSession, lot, sell
):
    _, brew = await add_product(db, Product(name="Cold brew"))
    await _recipe(db, batch="10", variation_id=brew.id)
    await sell((brew.id, "28", "4"), status=OrderStatus.SHIPPED, ordered_on=_days(-5))
    await lot(brew.id, "C-1", "14")
    await add_product(db, Product(name="Unsold"))
    await db.commit()

    made = await _read(client, initiative_stand_in, community_ref, plugin_manifest.MAKE_NEXT)

    assert made["item"] == []


async def _received(db: WriteSession, purchase: Purchase, days_later: int) -> None:
    """Date ``purchase``'s receipts ``days_later`` after it was ordered; they
    were entered today, which a lead time doesn't count."""
    await db.execute(
        update(PurchaseLine)
        .where(col(PurchaseLine.purchase_id) == purchase.id)
        .values(received_on=purchase.dated_on + timedelta(days_later))
    )


async def test_materials_to_order_expand_runs_through_bases_with_their_suppliers_lead_time(
    client,
    initiative_stand_in,
    community_ref,
    db: WriteSession,
    main,
    material,
    base,
    lot,
    buy,
    sell,
):
    for contact_id, name in ((7, "Mill & Co"), (8, "Lye Supply")):
        initiative_stand_in.routes[("GET", f"/contacts/{contact_id}")] = (
            lambda _r, contact_id=contact_id, name=name: httpx2.Response(
                200, json={"id": contact_id, "name": name, "is_organization": True}
            )
        )
    oil = await material("Olive oil", supplier_contact_id=7)
    lye = await material("Lye", supplier_contact_id=8)
    wax = await material("Wax", supplier_contact_id=7)
    solution = await base("Lye solution")
    soap_base = await base("Soap base")
    _, bar = await add_product(db, Product(name="Lavender soap"))
    _, candle = await add_product(db, Product(name="Candle"))
    await _recipe(db, (lye, "30"), batch="100", base_id=solution.id)
    await _recipe(db, (oil, "700"), (solution, "300"), batch="1000", base_id=soap_base.id)
    await _recipe(db, (soap_base, "1000"), batch="10", variation_id=bar.id)
    await _recipe(db, (wax, "500"), batch="10", variation_id=candle.id)
    # Expected sales of 30 bars, none available: 3 batches, 3000 g of soap
    # base, 2100 g of oil and 900 g of lye solution, which takes 270 g of lye.
    await sell((bar.id, "60", "8"), status=OrderStatus.SHIPPED, ordered_on=_days(-5))
    # A planned run of candles, due in 20 days, needs 1000 g of wax.
    await _open(db, candle.id, main, "2", deadline_on=_days(20))
    await lot(candle.id, "C-1", "100")
    # Mill & Co took 4, 6 and 10 days to deliver: 6 days, at the median. Its
    # order of oil not yet received counts as on its way.
    for ordered, took in ((-40, 4), (-30, 6), (-20, 10)):
        bought = await buy((wax, "100", "1"), dated_on=_days(ordered), supplier_contact_id=7)
        await _received(db, bought, took)
    on_order = Purchase(
        organization_id=db.organization_id,
        code="P-OPEN",
        dated_on=_days(-1),
        location_id=main.id,
        supplier_contact_id=7,
    )
    db.add(on_order)
    await db.flush()
    db.add(
        PurchaseLine(
            organization_id=db.organization_id,
            purchase_id=on_order.id,
            item_id=oil.id,
            purchase_quantity=Decimal(600),
            tracking_quantity=Decimal(600),
            unit_cost=Decimal(1),
        )
    )
    # Lye Supply has received nothing yet.
    await lot(lye.id, "L-1", "100")
    await db.commit()

    found = await _read(
        client, initiative_stand_in, community_ref, plugin_manifest.MATERIALS_TO_ORDER
    )

    assert found == {
        "material": ["Olive oil", "Lye", "Wax"],
        "unit": ["g", "g", "g"],
        "supplier": ["Mill & Co", "Lye Supply", "Mill & Co"],
        "short": ["1500.000000", "170.000000", "700.000000"],
        "needed_on": [TODAY.isoformat(), TODAY.isoformat(), _days(20).isoformat()],
        "order_by": [_days(-6).isoformat(), None, _days(14).isoformat()],
    }


async def test_production_hours_ahead_are_runs_by_deadline_and_suggested_runs(
    client, initiative_stand_in, community_ref, db: WriteSession, main, base, sell
):
    syrup = await base("Syrup")
    await _recipe(db, batch="1", minutes=60, base_id=syrup.id)
    _, brew = await add_product(db, Product(name="Cold brew"))
    await _recipe(db, batch="7", minutes=45, variation_id=brew.id)
    await sell((brew.id, "28", "4"), status=OrderStatus.SHIPPED, ordered_on=_days(-5))
    await _open(db, syrup.id, main, "2")
    await _open(db, syrup.id, main, status=RunStatus.IN_PROGRESS, deadline_on=_days(-3))
    await _open(db, syrup.id, main, deadline_on=_days(8))
    await _open(db, syrup.id, main, deadline_on=_days(30))
    await db.commit()

    hours = await _read(client, initiative_stand_in, community_ref, plugin_manifest.HOURS_AHEAD)

    # 14 cold brews expected: 2 batches of 45 minutes, suggested.
    assert hours == {
        "week": [_days(days).isoformat() for days in (0, 7, 14, 21)],
        "planned": ["3.00", "1.00", "0.00", "0.00"],
        "suggested": ["1.50", "0.00", "0.00", "0.00"],
    }


async def test_cost_to_make_is_the_last_two_runs_against_the_stores_price(
    client, initiative_stand_in, community_ref, db: WriteSession, main, material, lot
):
    soap = await material("Soap base")
    _, bar = await add_product(db, Product(name="Lavender soap"))
    _, candle = await add_product(db, Product(name="Candle"))
    await add_product(db, Product(name="Never made"))
    await _recipe(db, (soap, "2"), variation_id=bar.id)
    await _recipe(db, variation_id=candle.id)
    await lot(soap.id, "S-1", "4", date(2026, 11, 1), unit_cost="1")
    await runs.complete(db, await _open(db, bar.id, main), Decimal(1), date(2026, 11, 2))
    # The soap base's price went up: the next run cost more.
    await lot(soap.id, "S-2", "2", date(2026, 11, 3), unit_cost="4")
    await runs.complete(db, await _open(db, bar.id, main), Decimal(1), date(2026, 11, 4))
    await runs.complete(db, await _open(db, candle.id, main), Decimal(1), date(2026, 11, 4))
    await _open(db, bar.id, main)
    store = Store(organization_id=db.organization_id, channel=Channel.SHOPIFY, connected=True)
    db.add(store)
    await db.flush()
    db.add(
        Listing(
            organization_id=db.organization_id,
            store_id=store.id,
            product_id="p1",
            variant_id="v1",
            item_id=bar.id,
            matched_by=MatchedBy.HAND,
            price=Decimal(10),
        )
    )
    await db.commit()

    costs = await _read(client, initiative_stand_in, community_ref, plugin_manifest.COST_TO_MAKE)

    assert costs == {
        "product": ["Candle", "Lavender soap"],
        "last": ["0.000000", "5.000000"],
        "previous": [None, "2.000000"],
        "change": [None, "3.000000"],
        "price": [None, "10.000000"],
        "margin": [None, "0.500000"],
        "currency": "USD",
    }


async def test_expiring_soon_is_lots_holding_stock_soonest_first(
    client, initiative_stand_in, community_ref, db: WriteSession, main, material, base, lot, hold
):
    milk = await material("Oat milk")
    syrup = await base("Syrup")
    _, brew = await add_product(db, Product(name="Cold brew"))
    await lot(milk.id, "M-1", "5", expires_on=_days(3))
    await hold(await lot(syrup.id, "S-1", "1", expires_on=_days(-2)))
    await lot(brew.id, "C-1", "12", expires_on=_days(40))
    await lot(milk.id, "M-2", "6")
    used = await lot(milk.id, "M-3", "2", expires_on=_days(10))
    await adjust(
        db,
        Adjustment(
            item_id=milk.id,
            location_id=main.id,
            lot_id=used.id,
            kind=AdjustmentKind.MANUAL,
            dated_on=TODAY,
            change=Decimal(-2),
        ),
    )
    await db.commit()

    expiring = await _read(
        client, initiative_stand_in, community_ref, plugin_manifest.EXPIRING_SOON
    )

    assert expiring == {
        "lot": ["S-1", "M-1"],
        "item": ["Syrup", "Oat milk"],
        "unit": ["g", "g"],
        "expires_on": [_days(-2).isoformat(), _days(3).isoformat()],
        "days_left": [-2, 3],
        "left": ["1.000000", "5.000000"],
        "on_hold": [True, False],
    }


async def test_sales_by_week_are_what_each_product_sells_now_over_twelve_weeks(
    client, initiative_stand_in, community_ref, db: WriteSession, sell
):
    _, brew = await add_product(db, Product(name="Cold brew"))
    monday = TODAY - timedelta(days=TODAY.weekday())
    await sell((brew.id, "3", "4"), status=OrderStatus.SHIPPED, ordered_on=monday)
    refunded = await sell((brew.id, "5", "4"), status=OrderStatus.SHIPPED, ordered_on=monday)
    await _refund(db, refunded.id, "2")
    await sell((brew.id, "9", "4"), status=OrderStatus.CANCELLED, ordered_on=monday)
    await sell((brew.id, "4", "4"), status=OrderStatus.SHIPPED, ordered_on=monday - timedelta(1))
    await sell((brew.id, "7", "4"), ordered_on=monday - timedelta(weeks=12))
    await db.commit()

    sales = await _read(client, initiative_stand_in, community_ref, plugin_manifest.SALES_BY_WEEK)

    weeks = [monday - timedelta(weeks=11 - week) for week in range(12)]
    assert sales == {
        "product": ["Cold brew"] * 12,
        "week": [week.isoformat() for week in weeks],
        "units": ["0.000000"] * 10 + ["4.000000", "5.000000"],
    }
