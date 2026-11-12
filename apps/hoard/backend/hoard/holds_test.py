"""What a held lot keeps from use, and a hold's life: placed, with what was
made from its lot or from an order's lots, ready to check, then released or
discarded."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import holds, plugin_manifest, shipments, trace
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    DefaultHoldReason,
    DefaultReason,
    HoldEnd,
    Location,
    Lot,
    OutboxEvent,
    Product,
)
from hoard.stock import add_default_reasons, add_product
from hoard.trace import StepKind, Trace, TraceStep
from seneschal_core.db import WriteSession


async def test_only_open_holds_keep_a_lot_and_its_balance(
    db: WriteSession, main: Location, material, lot, hold
):
    flour = await material("Flour")
    shed = Location(organization_id=db.organization_id, name="Shed")
    db.add(shed)
    await db.flush()
    held = await lot(flour.id, "F-1", "4")
    away = await lot(flour.id, "F-2", "3", location=shed)
    released = await lot(flour.id, "F-3", "2")
    await lot(flour.id, "F-4", "5")
    await hold(held)
    await hold(away)
    await hold(released, ended=HoldEnd.RELEASED, ended_on=date(2026, 11, 3))

    assert await holds.held_lot_ids(db) == {held.id, away.id}
    assert await holds.held_lot_ids(db, flour.id, main.id) == {held.id}
    assert await holds.on_hold(db, flour.id) == 7
    assert await holds.on_hold(db, flour.id, shed.id) == 3


async def test_starting_or_ending_a_hold_notes_its_item_for_its_listings(db: WriteSession, lot):
    _, chai = await add_product(db, Product(name="Chai"))
    made = await lot(chai.id, "C-1", "6")

    await holds.note(db, made.id)

    assert db.info["hoard.noted_items"] == {db.organization_id: {chai.id}}
    assert await holds.on_hold(db, chai.id) == Decimal(0)


async def _events(db: WriteSession, name: str) -> list[dict]:
    found = await db.exec(
        select(OutboxEvent).where(OutboxEvent.name == name).order_by(col(OutboxEvent.id))
    )
    return [event.payload for event in found]


async def _reason(db: WriteSession, key: DefaultHoldReason) -> int:
    return await holds.reason_id(db, key)


async def test_a_hold_is_released_once_ready_or_its_lot_discarded(db: WriteSession, material, lot):
    flour = await material("Flour")
    await add_default_reasons(db)
    spoiled = (
        await db.exec(
            select(AdjustmentReason.id).where(AdjustmentReason.key == DefaultReason.EXPIRED.value)
        )
    ).one()
    suspect = await _reason(db, DefaultHoldReason.SUSPECT)
    waiting, partly, wholly = [await lot(flour.id, number, "6") for number in ("F-1", "F-2", "F-3")]

    hold = await holds.place(db, waiting, suspect, ready_on=date(2026, 11, 10), notes="Smell")
    with pytest.raises(holds.HoldRefused) as refused:
        await holds.place(db, waiting, suspect)
    assert refused.value.code is holds.HoldError.LOT_ON_HOLD
    with pytest.raises(holds.HoldRefused) as refused:
        await holds.release(db, hold, date(2026, 11, 9))
    assert refused.value.code is holds.HoldError.HOLD_NOT_READY
    await holds.release(db, hold, date(2026, 11, 10))
    assert (hold.ended, hold.ended_on) == (HoldEnd.RELEASED, date(2026, 11, 10))
    with pytest.raises(holds.HoldRefused) as refused:
        await holds.release(db, hold, date(2026, 11, 11))
    assert refused.value.code is holds.HoldError.HOLD_ENDED

    some = await holds.place(db, partly, suspect)
    with pytest.raises(holds.HoldRefused) as refused:
        await holds.discard(db, some, spoiled, date(2026, 11, 4), Decimal(7))
    assert refused.value.code is holds.HoldError.DISCARD_OVER_BALANCE
    await holds.discard(db, some, spoiled, date(2026, 11, 4), Decimal(2))
    every = await holds.place(db, wholly, suspect)
    await holds.discard(db, every, spoiled, date(2026, 11, 4))

    assert (some.ended, every.ended) == (HoldEnd.RELEASED, HoldEnd.DISCARDED)
    discarded = await db.exec(
        select(Adjustment.lot_id, Adjustment.change, Adjustment.reason_id, Adjustment.kind)
        .where(col(Adjustment.hold_id).is_not(None))
        .order_by(col(Adjustment.id))
    )
    assert discarded.all() == [
        (partly.id, -2, spoiled, AdjustmentKind.MANUAL),
        (wholly.id, -6, spoiled, AdjustmentKind.MANUAL),
    ]
    assert await holds.held_lot_ids(db) == set()
    placed, ended = await _events(db, "hold-placed"), await _events(db, "hold-ended")
    assert [event["lot"] for event in placed] == ["F-1", "F-2", "F-3"]
    assert (placed[0]["reason"], placed[0]["ready_on"]) == ("suspect", "2026-11-10")
    assert [(event["lot"], event["ended"]) for event in ended] == [
        ("F-1", "released"),
        ("F-2", "released"),
        ("F-3", "discarded"),
    ]


async def test_a_hold_is_ready_to_check_once_its_day_has_come(
    client, initiative_stand_in, community_ref, db: WriteSession, material, lot
):
    lye = await material("Lye")
    curing = await _reason(db, DefaultHoldReason.CURING)
    today = date.today()
    due, later, none = [await lot(lye.id, number, "1") for number in ("L-1", "L-2", "L-3")]
    await holds.place(db, due, curing, ready_on=today - timedelta(days=1))
    await holds.place(db, later, curing, ready_on=today + timedelta(days=1))
    await holds.place(db, none, curing)

    await holds.upkeep(db)
    await holds.upkeep(db)
    assert [event["lot"] for event in await _events(db, "hold-ready")] == ["L-1"]
    await holds.upkeep(db, today + timedelta(days=1))
    assert [event["lot"] for event in await _events(db, "hold-ready")] == ["L-1", "L-2"]
    await db.commit()

    token = initiative_stand_in.context(plugin_manifest.HOLDS_READY, community_ref=community_ref)
    answer = await client.post(
        "/v1/endpoints",
        json={"endpoint": plugin_manifest.HOLDS_READY, "params": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == {
        "lot": ["L-1"],
        "item": ["Lye"],
        "reason": ["curing"],
        "ready_on": [str(today - timedelta(days=1))],
    }


def _made(lot_id: int, made_lot_id: int, *, assumed: bool = False) -> TraceStep:
    return TraceStep(
        kind=StepKind.RUN,
        record_id=1,
        lot_id=lot_id,
        made_lot_id=made_lot_id,
        item_id=1,
        quantity=Decimal(1),
        dated_on=date(2026, 11, 2),
        contact_id=None,
        assumed=assumed,
        level=1,
    )


async def test_everything_made_from_a_lot_goes_on_hold_with_it(
    db: WriteSession, material, base, lot, hold, monkeypatch: pytest.MonkeyPatch
):
    flour, dough = await material("Flour"), await base("Dough")
    source = await lot(flour.id, "F-1", "5")
    made, maybe, held = [await lot(dough.id, number, "2") for number in ("D-1", "D-2", "D-4")]
    # A lot that holds nothing any more is left as it is.
    gone = Lot(
        organization_id=db.organization_id,
        item_id=dough.id,
        location_id=made.location_id,
        number="D-3",
        dated_on=date(2026, 11, 2),
    )
    db.add(gone)
    await db.flush()
    await hold(held)

    async def forward(_db: AsyncSession, lot_id: int) -> Trace:
        return Trace(
            lot_id,
            [
                _made(lot_id, made.id),
                _made(lot_id, maybe.id, assumed=True),
                _made(lot_id, gone.id),
                _made(lot_id, held.id),
            ],
        )

    monkeypatch.setattr(trace, "forward", forward)
    recall = await _reason(db, DefaultHoldReason.RECALL)
    first = await holds.place(db, source, recall, notes="Supplier recall")

    placed = await holds.place_made_from(db, first)

    assert [(each.lot_id, each.from_hold_id, each.reason_id) for each in placed] == [
        (made.id, first.id, recall),
        (maybe.id, first.id, recall),
    ]
    assert await holds.held_lot_ids(db, dough.id) == {made.id, maybe.id, held.id}


async def test_holding_an_order_holds_the_lots_its_shipments_drew(
    db: WriteSession, main: Location, lot, sell, hold
):
    _, chai = await add_product(db, Product(name="Chai"))
    first, second, already = [
        await lot(chai.id, number, "2", date(2026, 11, day))
        for number, day in (("C-1", 1), ("C-2", 2), ("C-3", 3))
    ]
    await lot(chai.id, "C-4", "9", date(2026, 11, 4))
    order = await sell((chai.id, "5", "9"))
    shipment = await shipments.add(db, order, main.id, [])
    await shipments.ship(db, shipment, date(2026, 11, 5))
    await hold(already)
    suspect = await _reason(db, DefaultHoldReason.SUSPECT)

    placed = await holds.place_from_order(db, order, suspect, notes="Sour", made_from=False)

    assert [(each.lot_id, each.order_id, each.notes) for each in placed] == [
        (first.id, order.id, "Sour"),
        (second.id, order.id, "Sour"),
    ]
    assert await holds.held_lot_ids(db, chai.id) == {first.id, second.id, already.id}
