"""Production runs through the API: opening, recording lots, completing and
reopening, costs only for who sees them, and nothing while Hoard is read-only."""

from __future__ import annotations

from decimal import Decimal

from hoard import plugin_manifest
from hoard.records import Base, Material, Recipe, RecipeLine
from hoard.stock import on_hand
from seneschal_core.db import WriteSession

RUNS = "/api/v1/runs"


async def _dough(db: WriteSession, base, material, buy) -> tuple[Material, Base]:
    """Flour, bought 10 at 2, and dough, made of 2 flour a batch of 4."""
    flour = await material("Flour")
    await buy((flour, "10", "2"))
    dough = await base("Dough")
    recipe = Recipe(organization_id=db.organization_id, base_id=dough.id, batch_quantity=Decimal(4))
    db.add(recipe)
    await db.flush()
    db.add(
        RecipeLine(
            organization_id=db.organization_id,
            recipe_id=recipe.id,
            item_id=flour.id,
            quantity=Decimal(2),
        )
    )
    await db.commit()
    return flour, dough


async def test_a_run_is_opened_completed_and_reopened(
    client, sign_in, db: WriteSession, base, material, buy
):
    flour, dough = await _dough(db, base, material, buy)
    await sign_in(client, (plugin_manifest.SEE_COSTS,), initiative_id=7)

    opened = await client.post(RUNS, json={"item_id": dough.id, "planned_batches": "2"})
    assert opened.status_code == 201, opened.text
    run = opened.json()
    assert run["code"] == f"R-{run['id']:04d}"
    assert (run["status"], run["initiative_id"], run["planned_quantity"]) == (
        "not_started",
        7,
        "8.000000",
    )
    [line] = run["lines"]
    assert (line["item"]["name"], line["quantity"], line["lot"], line["assumed"]) == (
        "Flour",
        "4.000000",
        None,
        False,
    )
    [offered] = line["lots"]
    assert offered["balance"] == "10.000000"
    assert "cost" not in line

    recorded = await client.patch(
        f"{RUNS}/{run['id']}/lines/{line['id']}", json={"lot_id": offered["id"]}
    )
    assert recorded.json()["lines"][0]["lot"]["id"] == offered["id"]

    started = await client.post(f"{RUNS}/{run['id']}/start", json={"started_on": "2026-11-02"})
    assert (started.json()["status"], started.json()["started_on"]) == ("in_progress", "2026-11-02")
    again = await client.post(f"{RUNS}/{run['id']}/start", json={})
    assert (again.status_code, again.json()) == (409, {"detail": "RUN_STARTED"})

    completed = await client.post(
        f"{RUNS}/{run['id']}/complete",
        json={"actual_quantity": "8", "completed_on": "2026-11-03"},
    )
    assert completed.status_code == 200, completed.text
    done = completed.json()
    assert (done["status"], done["output_lot"]["number"], done["output_lot"]["balance"]) == (
        "completed",
        done["code"],
        "8.000000",
    )
    assert (done["cost"], done["unit_cost"], done["uncosted"]) == ("8.000000", "1.000000", False)
    assert (done["lines"][0]["unit_cost"], done["lines"][0]["cost"]) == ("2.000000", "8.000000")
    assert (await on_hand(db, flour.id), await on_hand(db, dough.id)) == (6, 8)

    changing = await client.patch(f"{RUNS}/{run['id']}", json={"planned_batches": "3"})
    assert (changing.status_code, changing.json()) == (409, {"detail": "RUN_COMPLETED"})
    renamed = await client.patch(f"{RUNS}/{run['id']}", json={"code": "DOUGH-1"})
    assert renamed.json()["output_lot"]["number"] == "DOUGH-1"

    listed = await client.get(RUNS, params={"status": "completed"})
    assert [(summary["code"], summary["cost"]) for summary in listed.json()["items"]] == [
        ("DOUGH-1", "8.000000")
    ]

    reopened = await client.post(f"{RUNS}/{run['id']}/reopen")
    assert (reopened.json()["status"], reopened.json()["output_lot"]) == ("in_progress", None)
    assert (await on_hand(db, flour.id), await on_hand(db, dough.id)) == (10, 0)

    deleted = await client.delete(f"{RUNS}/{run['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"{RUNS}/{run['id']}")).status_code == 404


async def test_costs_are_sent_only_to_who_sees_them(
    client, sign_in, db: WriteSession, base, material, buy
):
    _, dough = await _dough(db, base, material, buy)
    await sign_in(client)
    run = (await client.post(RUNS, json={"item_id": dough.id, "start": True})).json()
    assert run["status"] == "in_progress"

    done = (await client.post(f"{RUNS}/{run['id']}/complete", json={"actual_quantity": "4"})).json()
    assert done["status"] == "completed"
    assert not {"cost", "unit_cost"} & done.keys()
    assert "unit_cost" not in done["lines"][0]
    listed = (await client.get(RUNS)).json()["items"]
    assert "cost" not in listed[0]


async def test_a_run_needs_a_recipe_and_a_free_code(
    client, sign_in, db: WriteSession, base, material, buy
):
    flour, dough = await _dough(db, base, material, buy)
    await sign_in(client)

    no_recipe = await client.post(RUNS, json={"item_id": flour.id})
    assert (no_recipe.status_code, no_recipe.json()) == (422, {"detail": "RUN_NO_RECIPE"})
    first = await client.post(RUNS, json={"item_id": dough.id, "code": "B-1"})
    assert first.status_code == 201
    taken = await client.post(RUNS, json={"item_id": dough.id, "code": "B-1"})
    assert (taken.status_code, taken.json()) == (409, {"detail": "RUN_CODE_TAKEN"})


async def test_runs_are_refused_while_hoard_is_read_only(
    client, sign_in, db: WriteSession, base, material, buy, initiative_stand_in, community_ref
):
    _, dough = await _dough(db, base, material, buy)
    await sign_in(client)
    run = (await client.post(RUNS, json={"item_id": dough.id})).json()
    token = initiative_stand_in.lifecycle("community_status", community_ref=community_ref)
    told = await client.post(
        "/v1/hooks/community_status",
        json={"status": "read_only"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert told.status_code == 204

    for refused in (
        await client.post(RUNS, json={"item_id": dough.id}),
        await client.post(f"{RUNS}/{run['id']}/complete", json={"actual_quantity": "4"}),
    ):
        assert (refused.status_code, refused.json()) == (403, {"detail": "READ_ONLY"})
    assert (await client.get(f"{RUNS}/{run['id']}")).status_code == 200
