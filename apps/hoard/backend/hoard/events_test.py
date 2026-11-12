"""Events are recorded with the change that caused them, in the shapes the
manifest declares, and stock falls to its limit once."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlmodel import select

from hoard import events
from hoard.events import Event, EventName
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Location,
    OutboxEvent,
    ProductionRun,
    PurchaseLine,
    TaskLink,
)
from hoard.stock import adjust
from seneschal_core.db import Database, WriteSession


async def _outbox(serving_database: Database, organization_id: int) -> list[OutboxEvent]:
    async with serving_database.organization_session(organization_id) as db:
        return list((await db.exec(select(OutboxEvent).order_by(OutboxEvent.id))).all())


async def test_an_event_is_kept_only_with_the_change_that_caused_it(
    serving_database: Database, db: WriteSession
):
    await events.record(db, Event(EventName.PURCHASE_PLACED, {"purchase_id": 1}, 12))
    await db.rollback()
    assert await _outbox(serving_database, db.organization_id) == []

    await events.record(db, Event(EventName.PURCHASE_PLACED, {"purchase_id": 2}, 12))
    await db.commit()
    [kept] = await _outbox(serving_database, db.organization_id)
    assert (kept.name, kept.payload, kept.initiative_id, kept.attempts) == (
        "purchase-placed",
        {"purchase_id": 2},
        12,
        0,
    )


async def test_a_runs_and_a_purchases_events_carry_what_they_declare_and_their_task(
    db: WriteSession, main: Location, material, base, buy
):
    flour = await material("Flour")
    dough = await base("Dough")
    purchase = await buy((flour, "10", "2"), initiative_id=12)
    [line] = (
        await db.exec(select(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
    ).all()
    run = ProductionRun(
        organization_id=db.organization_id,
        code="R-0001",
        item_id=dough.id,
        location_id=main.id,
        initiative_id=12,
        started_on=date(2026, 11, 2),
    )
    db.add(run)
    await db.flush()
    db.add(TaskLink(organization_id=db.organization_id, task_id=41, run_id=run.id))
    await db.flush()

    started = await events.run_event(db, EventName.RUN_STARTED, run)
    received = await events.purchase_event(db, EventName.PURCHASE_LINE_RECEIVED, purchase, line)

    for event in (started, received):
        assert list(event.payload) == [r.key for r in events.RETURNS[event.name]]
        assert event.initiative_id == 12
    assert (started.payload["item"], started.payload["started_on"]) == ("Dough", "2026-11-02")
    assert (started.payload["task_id"], received.payload["task_id"]) == (41, None)
    assert received.payload | {"purchase_id": 0, "line_id": 0} == {
        "purchase_id": 0,
        "code": purchase.code,
        "dated_on": "2026-11-01",
        "supplier_contact_id": None,
        "lines": 1,
        "lines_received": 1,
        "initiative_id": 12,
        "task_id": None,
        "line_id": 0,
        "material": "Flour",
        "quantity": "10.000000",
    }


async def test_stock_falling_to_its_limit_is_recorded_once_until_it_rises_again(
    serving_database: Database, db: WriteSession, main: Location, material, buy
):
    flour = await material("Flour", low_stock_limit=Decimal(5))
    await buy((flour, "10", "2"))

    async def use(change: str) -> None:
        await adjust(
            db,
            Adjustment(
                item_id=flour.id,
                location_id=main.id,
                kind=AdjustmentKind.MANUAL,
                dated_on=date(2026, 11, 2),
                change=Decimal(change),
            ),
        )
        await events.record_if_low(db, flour.id)

    for change in ("-4", "-2", "-1", "6", "-5"):
        await use(change)
    await db.commit()

    low = [
        event
        for event in await _outbox(serving_database, db.organization_id)
        if event.name == "stock-low"
    ]
    assert [event.payload["on_hand"] for event in low] == ["4.000000", "4.000000"]
    assert low[0].payload | {"id": 0} == {
        "kind": "material",
        "id": 0,
        "name": "Flour",
        "unit": "g",
        "on_hand": "4.000000",
        "low_stock_limit": "5.000000",
    }
    assert {event.name for event in low} == {"stock-low"}
