"""Stocktakes that count lots: a full count, or a spot check."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard.records import Adjustment, AdjustmentKind, Location, Lot
from hoard.stock import adjust
from seneschal_core.db import WriteSession

STOCKTAKES = "/api/v1/stocktakes"


async def start(client, lot_count: str | None) -> dict:
    started = await client.post(
        STOCKTAKES, json={"kind": "material", **({"lot_count": lot_count} if lot_count else {})}
    )
    assert started.status_code == 201, started.text
    return started.json()


def row_of(stocktake: dict, number: str | None, item: str = "Sencha") -> dict:
    return next(
        row
        for row in stocktake["rows"]
        if row["item"]["name"] == item and (row["lot"]["number"] if row["lot"] else None) == number
    )


async def balance(db: WriteSession, lot: Lot) -> Decimal:
    await db.refresh(lot)
    return lot.balance


async def test_a_full_count_counts_each_lot_and_finishes_the_uncounted_at_nothing(
    client, sign_in, db: WriteSession, material, lot
):
    sencha = await material("Sencha")
    matcha = await material("Matcha")
    first = await lot(sencha.id, "L-1", "5", date(2026, 10, 1))
    second = await lot(sencha.id, "L-2", "3", date(2026, 10, 2))
    untouched = await lot(matcha.id, "M-1", "7")
    await db.commit()
    await sign_in(client)

    stocktake = await start(client, "full")
    assert stocktake["lot_count"] == "full"
    assert [
        (row["lot"]["number"], row["on_hand"])
        for row in stocktake["rows"]
        if row["item"]["name"] == "Sencha"
    ] == [
        ("L-1", "5.000000"),
        ("L-2", "3.000000"),
    ]
    counted = await client.post(
        f"{STOCKTAKES}/{stocktake['id']}/rows/{row_of(stocktake, 'L-1')['id']}/confirm",
        json={"counted": "4"},
    )
    assert counted.status_code == 200, counted.text
    found = await lot(sencha.id, "L-3", "2")
    await db.commit()
    added = await client.post(f"{STOCKTAKES}/{stocktake['id']}/rows", json={"lot_id": found.id})
    assert row_of(added.json(), "L-3")["counted"] is None

    finished = await client.post(f"{STOCKTAKES}/{stocktake['id']}/finish")

    assert finished.status_code == 200, finished.text
    assert (await balance(db, first), await balance(db, second), await balance(db, found)) == (
        Decimal(4),
        Decimal(0),
        Decimal(0),
    )
    # An item nobody counted is left as it was.
    assert await balance(db, untouched) == Decimal(7)
    assert row_of(finished.json(), "M-1", "Matcha")["counted"] is None


async def test_a_spot_check_changes_only_the_lots_counted(
    client, sign_in, db: WriteSession, material, lot
):
    sencha = await material("Sencha")
    first = await lot(sencha.id, "L-1", "5")
    second = await lot(sencha.id, "L-2", "3")
    await db.commit()
    await sign_in(client)
    stocktake = await start(client, "spot")

    await client.post(
        f"{STOCKTAKES}/{stocktake['id']}/rows/{row_of(stocktake, 'L-1')['id']}/confirm",
        json={"counted": "6"},
    )
    await client.post(f"{STOCKTAKES}/{stocktake['id']}/finish")

    assert (await balance(db, first), await balance(db, second)) == (Decimal(6), Decimal(3))


async def test_stock_in_no_lot_is_a_row_of_its_own(
    client, sign_in, db: WriteSession, material, lot, main: Location
):
    sencha = await material("Sencha")
    await lot(sencha.id, "L-1", "5")
    await adjust(
        db,
        Adjustment(
            item_id=sencha.id,
            location_id=main.id,
            kind=AdjustmentKind.STARTING,
            dated_on=date(2026, 11, 1),
            change=Decimal(2),
            unit_cost=Decimal(1),
        ),
    )
    await db.commit()
    await sign_in(client)

    stocktake = await start(client, "full")

    assert row_of(stocktake, None)["on_hand"] == "2.000000"
    assert row_of(stocktake, "L-1")["on_hand"] == "5.000000"


async def test_a_lot_is_added_only_to_a_stocktake_counting_lots_at_its_location(
    client, sign_in, db: WriteSession, material, lot
):
    sencha = await material("Sencha")
    back = Location(organization_id=db.organization_id, name="Back room")
    db.add(back)
    await db.flush()
    elsewhere = await lot(sencha.id, "B-1", "1", location=back)
    here = await lot(sencha.id, "L-1", "1")
    await db.commit()
    await sign_in(client)
    by_item = await start(client, None)
    by_lot = await start(client, "spot")

    refused = await client.post(f"{STOCKTAKES}/{by_item['id']}/rows", json={"lot_id": here.id})
    mismatch = await client.post(f"{STOCKTAKES}/{by_lot['id']}/rows", json={"lot_id": elsewhere.id})

    assert (refused.status_code, refused.json()) == (409, {"detail": "STOCKTAKE_NOT_BY_LOT"})
    assert (mismatch.status_code, mismatch.json()) == (422, {"detail": "STOCKTAKE_LOT_MISMATCH"})
    assert by_item["lot_count"] is None
    assert [row["lot"] for row in by_item["rows"]] == [None]
