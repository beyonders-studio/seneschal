"""Holds over the API: placing one, with what was made from its lot or from an
order's lots, releasing and discarding it, and the hold reasons."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import select

from hoard import shipments, trace
from hoard.records import AdjustmentReason, DefaultReason, HoldReason, Location, Product
from hoard.stock import add_default_reasons, add_product
from hoard.trace import StepKind, Trace, TraceStep
from seneschal_core.db import WriteSession


async def test_the_hold_reasons_start_with_the_defaults_and_an_admin_changes_them(client, sign_in):
    await sign_in(client)
    listed = (await client.get("/api/v1/holds/reasons")).json()
    assert sorted(reason["key"] for reason in listed) == [
        "awaiting_results",
        "curing",
        "recall",
        "suspect",
    ]
    refused = await client.post("/api/v1/holds/reasons", json={"name": "Mould"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    mould = (await client.post("/api/v1/holds/reasons", json={"name": "Mould"})).json()
    taken = await client.post("/api/v1/holds/reasons", json={"name": "Mould"})
    renamed = await client.patch(
        f"/api/v1/holds/reasons/{listed[0]['id']}", json={"name": "Lab tests"}
    )
    removed = await client.delete(f"/api/v1/holds/reasons/{mould['id']}")

    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    assert renamed.json()["name"] == "Lab tests"
    assert removed.status_code == 204


async def test_a_lot_is_held_with_what_was_made_from_it_then_released_or_discarded(
    client, sign_in, db: WriteSession, material, base, lot, monkeypatch: pytest.MonkeyPatch
):
    flour, dough = await material("Flour"), await base("Dough")
    source = await lot(flour.id, "F-1", "5")
    made = await lot(dough.id, "D-1", "4")
    await add_default_reasons(db)
    spoiled = (
        await db.exec(
            select(AdjustmentReason.id).where(AdjustmentReason.key == DefaultReason.EXPIRED.value)
        )
    ).one()
    await db.commit()

    async def forward(_db, lot_id: int) -> Trace:
        step = TraceStep(
            kind=StepKind.RUN,
            record_id=1,
            lot_id=lot_id,
            made_lot_id=made.id,
            item_id=flour.id,
            quantity=Decimal(1),
            dated_on=date(2026, 11, 2),
            contact_id=None,
            assumed=False,
            level=1,
        )
        return Trace(lot_id, [step])

    monkeypatch.setattr(trace, "forward", forward)
    await sign_in(client)
    reasons = {r["key"]: r["id"] for r in (await client.get("/api/v1/holds/reasons")).json()}

    placed = await client.post(
        "/api/v1/holds",
        json={
            "lot_id": source.id,
            "reason_id": reasons["recall"],
            "ready_on": "2026-11-20",
            "notes": "Supplier recall",
            "made_from": True,
        },
    )
    assert placed.status_code == 201, placed.text
    first, second = placed.json()
    assert (first["lot"], first["ready_on"], first["ended"]) == ("F-1", "2026-11-20", None)
    assert (second["lot"], second["from_hold_id"]) == ("D-1", first["id"])
    again = await client.post(
        "/api/v1/holds", json={"lot_id": source.id, "reason_id": reasons["recall"]}
    )
    assert (again.status_code, again.json()) == (409, {"detail": "LOT_ON_HOLD"})
    held = (await client.get(f"/api/v1/lots/{source.id}")).json()
    assert (held["on_hold"], held["hold"]["id"]) == (True, first["id"])

    early = await client.post(
        f"/api/v1/holds/{first['id']}/release", json={"ended_on": "2026-11-19"}
    )
    assert (early.status_code, early.json()) == (409, {"detail": "HOLD_NOT_READY"})
    released = await client.post(
        f"/api/v1/holds/{first['id']}/release", json={"ended_on": "2026-11-20"}
    )
    assert (released.json()["ended"], released.json()["ended_on"]) == ("released", "2026-11-20")
    discarded = await client.post(
        f"/api/v1/holds/{second['id']}/discard", json={"reason_id": spoiled, "quantity": "1"}
    )
    assert discarded.json()["ended"] == "released"
    lot_after = (await client.get(f"/api/v1/lots/{made.id}")).json()
    assert (lot_after["on_hold"], Decimal(lot_after["balance"])) == (False, 3)


async def test_an_order_with_a_problem_holds_the_lots_it_drew(
    client, sign_in, db: WriteSession, main: Location, lot, sell
):
    _, chai = await add_product(db, Product(name="Chai"))
    drawn = await lot(chai.id, "C-1", "5")
    order = await sell((chai.id, "2", "4"))
    shipment = await shipments.add(db, order, main.id, [])
    await shipments.ship(db, shipment, date(2026, 11, 5))
    await db.commit()
    await sign_in(client)

    placed = await client.post(f"/api/v1/holds/orders/{order.id}", json={"notes": "Sour"})

    assert placed.status_code == 201, placed.text
    [hold] = placed.json()
    suspect = (await db.exec(select(HoldReason).where(HoldReason.key == "suspect"))).one()
    assert (hold["lot_id"], hold["order_id"], hold["reason_id"], hold["notes"]) == (
        drawn.id,
        order.id,
        suspect.id,
        "Sour",
    )
    read = (await client.get(f"/api/v1/orders/{order.id}")).json()
    assert read["shipments"][0]["lines"][0]["lot"]["on_hold"] is True
