"""Tasks track runs and purchases: another plug-in links one for a member, a
task's buttons complete a run or receive a purchase, and events reach
Initiative after the commit that recorded them, retried until it takes them."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
from fastapi import FastAPI
from sqlalchemy import update
from sqlmodel import col, select

from hoard import events, plugin_manifest, runs, tracking
from hoard.events import Event, EventName
from hoard.records import (
    Location,
    OutboxEvent,
    ProductionRun,
    Purchase,
    PurchaseLine,
    RunStatus,
    TaskLink,
)
from seneschal_core.api import Plugin
from seneschal_core.db import Database, WriteSession
from seneschal_core.initiative_api import INSTALLATION_PREFIX
from seneschal_core.testing import InitiativeStandIn

#: The automations plug-in, as Initiative names the caller of a public endpoint.
AUTOMATIONS = "plugins:automations"
EMIT = ("POST", f"{INSTALLATION_PREFIX}/events")


async def _call(
    client, initiative_stand_in, community_ref: str, endpoint: str, params=None, **claims: Any
):
    token = initiative_stand_in.context(endpoint, community_ref=community_ref, **claims)
    return await client.post(
        "/v1/endpoints",
        json={"endpoint": endpoint, "params": params or {}},
        headers={"Authorization": f"Bearer {token}"},
    )


def _for_member(member: str = "member-2", **claims: Any) -> dict[str, Any]:
    """The claims of a call the automations plug-in makes for ``member``."""
    return {"act": {"sub": AUTOMATIONS}, "actor": "member", "member": member, **claims}


async def _run(db: WriteSession, base, main: Location, **fields: Any) -> ProductionRun:
    dough = await base("Dough")
    run = ProductionRun(
        organization_id=db.organization_id,
        code="R-0001",
        item_id=dough.id,
        location_id=main.id,
        initiative_id=12,
        started_on=date(2026, 11, 2),
        **fields,
    )
    db.add(run)
    await db.flush()
    return run


async def _purchase(db: WriteSession, main: Location, *materials) -> Purchase:
    """A purchase in initiative 12 of one line of each material, none received."""
    purchase = Purchase(
        organization_id=db.organization_id,
        code="P-0001",
        dated_on=date(2026, 11, 1),
        location_id=main.id,
        initiative_id=12,
    )
    db.add(purchase)
    await db.flush()
    for bought in materials:
        db.add(
            PurchaseLine(
                organization_id=db.organization_id,
                purchase_id=purchase.id,
                material_id=bought.id,
                purchase_quantity=Decimal(2),
                tracking_quantity=Decimal(2),
                unit_cost=Decimal(1),
            )
        )
    await db.flush()
    return purchase


def _track(db: WriteSession, task_id: int, **record: int) -> None:
    db.add(TaskLink(organization_id=db.organization_id, task_id=task_id, **record))


async def _outbox(serving_database: Database, organization_id: int) -> list[OutboxEvent]:
    async with serving_database.organization_session(organization_id) as db:
        return list((await db.exec(select(OutboxEvent).order_by(col(OutboxEvent.id)))).all())


async def test_a_member_links_a_task_and_hoard_writes_its_fields(
    client, initiative_stand_in: InitiativeStandIn, community_ref, db: WriteSession, base, main
):
    run = await _run(db, base, main, deadline_on=date(2026, 11, 9), assignee_ref="maker")
    await db.commit()

    answer = await _call(
        client,
        initiative_stand_in,
        community_ref,
        plugin_manifest.LINK_TASK,
        {"record": "run", "record_id": run.id, "task_id": 41},
        **_for_member(initiative_id=12),
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == {"record": "run", "record_id": run.id, "task_id": 41}
    link = (await db.exec(select(TaskLink).where(TaskLink.run_id == run.id))).one()
    assert (link.task_id, link.created_by) == (41, "member-2")
    assert initiative_stand_in.metadata[(community_ref, "task", 41)] == {
        "hoard.record": "R-0001",
        "hoard.status": "not_started",
        "hoard.item": "Dough",
        "hoard.assignee": "maker",
        "hoard.started": "2026-11-02",
        "hoard.deadline": "2026-11-09",
    }
    assert list(initiative_stand_in.narrowed.values()) == [12]


async def test_linking_refuses_another_initiatives_record_and_a_task_tracking_another(
    client, initiative_stand_in, community_ref, db: WriteSession, base, main, material
):
    run = await _run(db, base, main)
    purchase = await _purchase(db, main, await material("Flour"))
    _track(db, 41, run_id=run.id)
    await db.commit()

    async def link(record: str, record_id: int, task_id: int, initiative_id: int):
        return await _call(
            client,
            initiative_stand_in,
            community_ref,
            plugin_manifest.LINK_TASK,
            {"record": record, "record_id": record_id, "task_id": task_id},
            **_for_member(initiative_id=initiative_id),
        )

    elsewhere = await link("purchase", purchase.id, 42, 13)
    taken = await link("purchase", purchase.id, 41, 12)
    unknown = await link("lot", 1, 42, 12)

    assert (elsewhere.status_code, elsewhere.json()) == (404, {"detail": "NOT_FOUND"})
    assert (taken.status_code, taken.json()) == (409, {"detail": "TASK_TRACKS_ANOTHER"})
    assert (unknown.status_code, unknown.json()) == (400, {"detail": "REQUEST_INVALID"})


async def test_a_read_only_community_links_and_completes_nothing(
    client, initiative_stand_in, community_ref, db: WriteSession, base, main
):
    run = await _run(db, base, main)
    _track(db, 41, run_id=run.id)
    await db.commit()

    linked = await _call(
        client,
        initiative_stand_in,
        community_ref,
        plugin_manifest.LINK_TASK,
        {"record": "run", "record_id": run.id, "task_id": 42},
        **_for_member(community_status="read_only"),
    )
    completed = await _call(
        client,
        initiative_stand_in,
        community_ref,
        plugin_manifest.COMPLETE_RUN,
        {"actual_quantity": "5"},
        viewer="viewer-1",
        subject={"type": "task", "id": 41},
        community_status="on_hold",
    )

    for refused in (linked, completed):
        assert (refused.status_code, refused.json()) == (403, {"detail": "READ_ONLY"})
    assert initiative_stand_in.metadata == {}


async def test_a_tasks_button_completes_its_run_at_the_actual_quantity(
    client, initiative_stand_in, community_ref, db: WriteSession, base, main, monkeypatch
):
    run = await _run(db, base, main)
    _track(db, 41, run_id=run.id)
    await db.commit()
    asked: list[tuple[Decimal, date, str | None]] = []

    async def complete(
        db: WriteSession, run: ProductionRun, actual_quantity: Decimal, completed_on: date
    ) -> None:
        asked.append((actual_quantity, completed_on, db.actor))
        run.status = RunStatus.COMPLETED
        run.actual_quantity = actual_quantity
        run.completed_on = completed_on

    monkeypatch.setattr(runs, "complete", complete)

    async def press(quantity: object, task_id: int = 41):
        return await _call(
            client,
            initiative_stand_in,
            community_ref,
            plugin_manifest.COMPLETE_RUN,
            {"actual_quantity": quantity},
            viewer="viewer-1",
            subject={"type": "task", "id": task_id},
        )

    invalid = await press("a dozen")
    untracked = await press("12", task_id=99)
    answer = await press("12.5")
    again = await press("12.5")

    assert (invalid.status_code, untracked.status_code) == (400, 404)
    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == {"run_id": run.id, "code": "R-0001", "status": "completed"}
    assert asked == [(Decimal("12.5"), date.today(), "viewer-1")]
    assert (again.status_code, again.json()) == (409, {"detail": "RUN_COMPLETED"})


async def test_a_tasks_button_receives_its_purchase_in_full(
    client,
    initiative_stand_in,
    community_ref,
    serving_database: Database,
    db: WriteSession,
    main,
    material,
):
    purchase = await _purchase(db, main, await material("Flour"), await material("Salt"))
    _track(db, 41, purchase_id=purchase.id)
    await db.commit()

    answer = await _call(
        client,
        initiative_stand_in,
        community_ref,
        plugin_manifest.RECEIVE_PURCHASE,
        viewer="viewer-1",
        subject={"type": "task", "id": 41},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == {
        "purchase_id": purchase.id,
        "code": "P-0001",
        "lines_received": 2,
    }
    lines = await db.exec(
        select(PurchaseLine.received, PurchaseLine.updated_by).where(
            PurchaseLine.purchase_id == purchase.id
        )
    )
    assert lines.all() == [(True, "viewer-1"), (True, "viewer-1")]
    recorded = await _outbox(serving_database, db.organization_id)
    assert [(event.name, event.payload["lines_received"]) for event in recorded] == [
        ("purchase-line-received", 1),
        ("purchase-line-received", 2),
        ("purchase-received", 2),
    ]


async def _until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while not await check():
            await asyncio.sleep(0.02)


async def test_events_are_sent_after_the_commit_and_retried_in_order(
    app: FastAPI,
    initiative_stand_in: InitiativeStandIn,
    community_ref,
    serving_database: Database,
    db: WriteSession,
    main,
    material,
):
    plugin: Plugin = app.state.plugin
    purchase = await _purchase(db, main, await material("Flour"))
    _track(db, 41, purchase_id=purchase.id)
    await db.commit()
    emit = initiative_stand_in.routes[EMIT]
    initiative_stand_in.routes[EMIT] = lambda _request: httpx2.Response(503)
    delivering = asyncio.create_task(tracking.deliver_forever(plugin, sweep_seconds=3600))
    try:
        placed = await events.purchase_event(db, EventName.PURCHASE_PLACED, purchase)
        await events.record(db, placed)
        await events.record(db, Event(EventName.STOCK_LOW, {"kind": "material", "id": 1}))
        await db.commit()

        async def tried() -> bool:
            held = await _outbox(serving_database, db.organization_id)
            return [event.attempts for event in held] == [1, 0]

        await _until(tried)
        assert initiative_stand_in.events == []

        initiative_stand_in.routes[EMIT] = emit
        await db.execute(
            update(OutboxEvent).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        await tracking.deliver(plugin, db.organization_id)
    finally:
        delivering.cancel()

    assert await _outbox(serving_database, db.organization_id) == []
    sent = [body for community, body in initiative_stand_in.events if community == community_ref]
    assert [body["event_type"] for body in sent] == [
        plugin_manifest.event_id(EventName.PURCHASE_PLACED),
        plugin_manifest.event_id(EventName.STOCK_LOW),
    ]
    assert (sent[0]["payload"]["task_id"], sent[0]["initiative_id"]) == (41, 12)
    assert initiative_stand_in.metadata[(community_ref, "task", 41)] == {
        "hoard.record": "P-0001",
        "hoard.status": "ordered",
        "hoard.ordered": "2026-11-01",
        "hoard.received": {"value": 0, "max": 1},
    }


async def test_an_event_initiative_refuses_is_dropped(
    app: FastAPI, initiative_stand_in, community_ref, serving_database: Database, db
):
    initiative_stand_in.routes[EMIT] = lambda _request: httpx2.Response(
        422, json={"detail": "UNKNOWN_EVENT_TYPE"}
    )
    await events.record(db, Event(EventName.STOCK_LOW, {"kind": "material", "id": 1}))
    await db.commit()

    await tracking.deliver(app.state.plugin, db.organization_id)

    assert await _outbox(serving_database, db.organization_id) == []
