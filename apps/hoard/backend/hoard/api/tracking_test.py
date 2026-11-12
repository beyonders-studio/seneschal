"""Settings' view of tracking: what is tracked, what waits, and sending it now."""

from __future__ import annotations

from datetime import date

import httpx2

from hoard import events
from hoard.events import Event, EventName
from hoard.records import Purchase, TaskLink
from seneschal_core.db import WriteSession
from seneschal_core.initiative_api import INSTALLATION_PREFIX

EMIT = ("POST", f"{INSTALLATION_PREFIX}/events")


async def test_an_administrator_sends_the_waiting_events_now(
    client, sign_in, initiative_stand_in, db: WriteSession, main
):
    purchase = Purchase(
        organization_id=db.organization_id,
        code="P-0001",
        dated_on=date(2026, 11, 1),
        location_id=main.id,
    )
    db.add(purchase)
    await db.flush()
    db.add(TaskLink(organization_id=db.organization_id, task_id=41, purchase_id=purchase.id))
    await events.record(db, Event(EventName.STOCK_LOW, {"kind": "material", "id": 1}))
    await db.commit()
    emit = initiative_stand_in.routes[EMIT]
    initiative_stand_in.routes[EMIT] = lambda _request: httpx2.Response(503)

    await sign_in(client)
    read = await client.get("/api/v1/tracking")
    refused = await client.post("/api/v1/tracking/send")
    await sign_in(client, admin=True)
    failed = await client.post("/api/v1/tracking/send")
    initiative_stand_in.routes[EMIT] = emit
    sent = await client.post("/api/v1/tracking/send")

    assert read.json() == {
        "runs": 0,
        "purchases": 1,
        "waiting": 1,
        "failing": 0,
        "retry_at": None,
    }
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})
    assert (failed.json()["waiting"], failed.json()["failing"]) == (1, 1)
    assert failed.json()["retry_at"] is not None
    assert (sent.json()["waiting"], sent.json()["failing"]) == (0, 0)
    assert len(initiative_stand_in.events) == 1
