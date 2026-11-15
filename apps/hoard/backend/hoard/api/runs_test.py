"""Production runs through the API: opening, recording lots, completing and
reopening, costs only for who sees them, nothing while Hoard is read-only, and
making as much as one ingredient allows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import plugin_manifest, recipes
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
            written=Decimal(2),
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


async def test_a_run_makes_as_much_as_one_ingredient_allows(
    client, sign_in, db: WriteSession, unit, base, material, lot
):
    catnip, chamomile, rose_hip, lychee = [
        await material(name) for name in ("Catnip", "Chamomile", "Rose hip", "Lychee")
    ]
    alyx = await base("Alyx batch")
    await recipes.save(
        db,
        Recipe(
            base_id=alyx.id,
            by_ratio=True,
            total=Decimal(931),
            total_unit_id=unit.id,
            batch_quantity=Decimal(931),
        ),
        [
            recipes.Line(catnip.id, Decimal(2)),
            recipes.Line(chamomile.id, Decimal(1)),
            recipes.Line(rose_hip.id, Decimal(1)),
            recipes.Line(lychee.id, Decimal("0.1")),
        ],
    )
    for item, quantity in ((catnip, "500"), (chamomile, "1000"), (rose_hip, "300"), (lychee, "50")):
        await lot(item.id, f"L-{item.id}", quantity)
    await lot(rose_hip.id, "OLD", "100", expires_on=date(2020, 1, 1))
    await db.commit()
    await sign_in(client)

    preview = (
        await client.post(
            RUNS + "/preview", json={"item_id": alyx.id, "use": {"item_id": rose_hip.id}}
        )
    ).json()

    assert (preview["use"]["amount"], preview["use"]["usable"], preview["use"]["expired"]) == (
        "300.000000",
        "300.000000",
        "100.000000",
    )
    assert [(line["quantity"], line["short"]) for line in preview["lines"]] == [
        ("600.000000", "100.000000"),
        ("300.000000", None),
        ("300.000000", None),
        ("30.000000", None),
    ]
    assert (preview["quantity"], preview["use"]["most"]) == ("1230.000000", "250.000000")
    nothing = (
        await client.post(
            RUNS + "/preview",
            json={"item_id": alyx.id, "use": {"item_id": rose_hip.id, "amount": "0.1"}},
        )
    ).json()
    assert nothing["lines"][0]["short"] is None

    opened = await client.post(
        RUNS, json={"item_id": alyx.id, "use": {"item_id": rose_hip.id, "amount": "300"}}
    )
    assert [line["quantity"] for line in opened.json()["lines"]] == [
        "600.000000",
        "300.000000",
        "300.000000",
        "30.000000",
    ]
    assert opened.json()["lines"][0]["short"] == "100.000000"
    assert opened.json()["recipe_changed"] is False
    recipe_id = opened.json()["recipe_id"]
    written = {
        "batch_quantity": "931",
        "by_ratio": True,
        "total": "931",
        "total_unit_id": unit.id,
        "lines": [
            {"item_id": item.id, "written": parts}
            for item, parts in ((catnip, "2"), (chamomile, "1"), (rose_hip, "1"), (lychee, "0.1"))
        ],
    }
    run_url = f"{RUNS}/{opened.json()['id']}"
    assert (await client.put(f"/api/v1/recipes/{recipe_id}", json=written)).status_code == 200
    assert (await client.get(run_url)).json()["recipe_changed"] is False
    written["lines"][3]["written"] = "0.2"
    assert (await client.put(f"/api/v1/recipes/{recipe_id}", json=written)).status_code == 200
    assert (await client.get(run_url)).json()["recipe_changed"] is True
    missing = await client.post(
        RUNS, json={"item_id": alyx.id, "use": {"item_id": alyx.id, "amount": "1"}}
    )
    assert (missing.status_code, missing.json()) == (422, {"detail": "RUN_NOT_IN_RECIPE"})
