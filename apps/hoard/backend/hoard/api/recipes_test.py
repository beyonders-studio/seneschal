"""Recipes are added, read scaled with their shares by weight, listed by what
they make, edited and removed; a bundle's holds variations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import plugin_manifest, recipes
from hoard.records import (
    Adjustment,
    AdjustmentKind,
    Location,
    OrganizationSettings,
    Product,
    Recipe,
    Unit,
)
from hoard.stock import add_product, adjust
from seneschal_core.db import WriteSession


async def test_a_recipe_is_added_read_scaled_edited_and_removed(
    client, sign_in, db: WriteSession, material, base
):
    tea = await material("Black tea")
    spice = await base("Chai spice")
    syrup = await base("Syrup")
    latte, latte_one = await add_product(db, Product(name="Chai latte"))
    await db.commit()
    await sign_in(client)

    made = await client.post(
        "/api/v1/recipes",
        json={
            "base_id": spice.id,
            "batch_quantity": "500",
            "minutes": 20,
            "notes": "Toast the spice, then stir in the syrup.",
            "lines": [
                {"item_id": tea.id, "written": "300"},
                {"item_id": syrup.id, "written": "200"},
            ],
        },
    )
    assert made.status_code == 201, made.text
    recipe = made.json()
    assert (recipe["output"], recipe["output_id"], recipe["name"], recipe["batches"]) == (
        "base",
        spice.id,
        "Chai spice",
        "1",
    )
    assert recipe["notes"] == "Toast the spice, then stir in the syrup."
    assert [(line["item"]["name"], line["percent"]) for line in recipe["lines"]] == [
        ("Black tea", "60.00"),
        ("Syrup", "40.00"),
    ]
    scaled = (await client.get(f"/api/v1/recipes/{recipe['id']}", params={"quantity": "50"})).json()
    assert (scaled["batch_quantity"], [line["quantity"] for line in scaled["lines"]]) == (
        "50.000000",
        ["30.000000", "20.000000"],
    )

    twice = await client.post("/api/v1/recipes", json={"base_id": spice.id, "lines": []})
    assert (twice.status_code, twice.json()) == (409, {"detail": "RECIPE_EXISTS"})
    neither = await client.post("/api/v1/recipes", json={"lines": []})
    assert (neither.status_code, neither.json()) == (422, {"detail": "REQUEST_INVALID"})
    cycle = await client.post(
        "/api/v1/recipes",
        json={"base_id": syrup.id, "lines": [{"item_id": spice.id, "written": "1"}]},
    )
    assert (cycle.status_code, cycle.json()) == (422, {"detail": "RECIPE_CYCLE"})
    product = await client.post(
        "/api/v1/recipes",
        json={"variation_id": latte_one.id, "lines": [{"item_id": latte_one.id, "written": "1"}]},
    )
    assert (product.status_code, product.json()) == (422, {"detail": "RECIPE_LINE_KIND"})
    await client.post(
        "/api/v1/recipes",
        json={"variation_id": latte_one.id, "lines": [{"item_id": spice.id, "written": "5"}]},
    )

    edited = await client.put(
        f"/api/v1/recipes/{recipe['id']}",
        json={"batch_quantity": "250", "lines": [{"item_id": tea.id, "written": "250"}]},
    )
    assert [line["quantity"] for line in edited.json()["lines"]] == ["250.000000"]
    assert edited.json()["notes"] is None
    of_latte = (await client.get("/api/v1/recipes", params={"product_id": latte.id})).json()
    assert [(each["output"], each["product_id"]) for each in of_latte["items"]] == [
        ("variation", latte.id)
    ]
    listed = (await client.get("/api/v1/recipes", params={"q": "chai"})).json()
    assert [each["name"] for each in listed["items"]] == ["Chai latte", "Chai spice"]

    assert (await client.delete(f"/api/v1/recipes/{recipe['id']}")).status_code == 204
    assert (await client.get("/api/v1/recipes", params={"base_id": spice.id})).json()["total"] == 0


async def test_a_bundle_s_recipe_holds_variations_and_marks_its_product(
    client, sign_in, db: WriteSession, material
):
    box, _ = await add_product(db, Product(name="Tea box"))
    _, tin = await add_product(db, Product(name="Tin of oolong"))
    tea = await material("Oolong")
    await db.commit()
    await sign_in(client)

    refused = await client.post(
        "/api/v1/recipes",
        json={"product_id": box.id, "lines": [{"item_id": tea.id, "written": "1"}]},
    )
    made = await client.post(
        "/api/v1/recipes",
        json={"product_id": box.id, "lines": [{"item_id": tin.id, "written": "3"}]},
    )

    assert (refused.status_code, refused.json()) == (422, {"detail": "RECIPE_LINE_KIND"})
    bundle = made.json()
    assert (bundle["output"], bundle["name"], bundle["unit_id"]) == ("bundle", "Tea box", None)
    assert [(line["item"]["kind"], line["percent"]) for line in bundle["lines"]] == [
        ("variation", None)
    ]
    await db.refresh(box)
    assert box.bundle


async def test_a_ratio_reads_beside_its_amounts_and_a_draft_switches_both_ways(
    client, sign_in, db: WriteSession, unit, material, base
):
    catnip, chamomile, rose_hip, lychee = [
        await material(name) for name in ("Catnip", "Chamomile", "Rose hip", "Lychee")
    ]
    alyx = await base("Alyx batch")
    kilograms = Unit(organization_id=db.organization_id, name="kg", standard="kilogram")
    db.add(kilograms)
    await db.commit()
    await sign_in(client)
    lines = [
        {"item_id": catnip.id, "written": "2"},
        {"item_id": chamomile.id, "written": "1"},
        {"item_id": rose_hip.id, "written": "1"},
        {"item_id": lychee.id, "written": "0.1"},
    ]

    ratio = {
        "base_id": alyx.id,
        "by_ratio": True,
        "total": "931",
        "total_unit_id": unit.id,
        "lines": lines,
    }
    made = await client.post("/api/v1/recipes", json={**ratio, "batch_quantity": "908"})
    recipe = made.json()
    used = await client.get(
        f"/api/v1/recipes/{recipe['id']}",
        params={"ingredient_id": rose_hip.id, "amount": "0.3", "unit_id": kilograms.id},
    )

    assert made.status_code == 201, made.text
    assert (recipe["total"], recipe["total_unit_id"]) == ("931.000000", unit.id)
    assert [(line["ratio"], line["amount"]) for line in recipe["lines"]] == [
        ("2.000000", "454.146341"),
        ("1.000000", "227.073171"),
        ("1.000000", "227.073171"),
        ("0.100000", "22.707317"),
    ]
    assert [line["quantity"] for line in used.json()["lines"]] == [
        "600.000000",
        "300.000000",
        "300.000000",
        "30.000000",
    ]
    by_amount = (await client.post("/api/v1/recipes/preview", json=ratio)).json()["switched"]
    assert (by_amount["by_ratio"], by_amount["total"]) == (False, None)
    assert [line["written"] for line in by_amount["lines"]] == [
        "454.146341",
        "227.073171",
        "227.073171",
        "22.707317",
    ]

    preview = (
        await client.post(
            "/api/v1/recipes/preview",
            json={
                "base_id": alyx.id,
                "batch_quantity": "908",
                "lines": [
                    {"item_id": catnip.id, "written": "0.454", "unit_id": kilograms.id},
                    {"item_id": chamomile.id, "written": "227"},
                    {"item_id": rose_hip.id, "written": "227"},
                ],
            },
        )
    ).json()
    assert [(line["ratio"], line["quantity"]) for line in preview["lines"]] == [
        ("1.000000", "454.000000"),
        ("500.000000", "227.000000"),
        ("500.000000", "227.000000"),
    ]
    by_ratio = preview["switched"]
    assert (by_ratio["by_ratio"], by_ratio["total"], by_ratio["total_unit_id"]) == (
        True,
        "908.000000",
        unit.id,
    )
    # Parts are each line's number in its own unit, so 0.454 kg stays 0.454.
    assert [line["written"] for line in by_ratio["lines"]] == [
        "0.454000",
        "227.000000",
        "227.000000",
    ]
    in_grams = (
        await client.post(
            "/api/v1/recipes/preview",
            json={
                "base_id": alyx.id,
                "lines": [
                    {"item_id": item.id, "written": written}
                    for item, written in ((catnip, "454"), (chamomile, "227"), (rose_hip, "227"))
                ],
            },
        )
    ).json()["switched"]
    assert [line["written"] for line in in_grams["lines"]] == ["2.000000", "1.000000", "1.000000"]


async def test_a_recipe_is_costed_only_for_who_sees_costs(
    client, sign_in, db: WriteSession, main: Location, material, base, buy, lot
):
    catnip, rose_hip, lychee = [
        await material(name) for name in ("Catnip", "Rose hip", "Lychee flavor")
    ]
    await buy((catnip, "1000", "0.02"), (rose_hip, "10", "0.01"))
    # Rose hips used up: their average is nothing, their last known cost 0.01.
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
    await lot(lychee.id, "L1", "40", unit_cost="0")
    alyx = await base("Alyx batch")
    recipe = await recipes.save(
        db,
        Recipe(base_id=alyx.id, batch_quantity=Decimal(931), minutes=30),
        [
            recipes.Line(catnip.id, Decimal(600)),
            recipes.Line(rose_hip.id, Decimal(300)),
            recipes.Line(lychee.id, Decimal(30)),
        ],
    )
    db.add(OrganizationSettings(organization_id=db.organization_id, labour_rate=Decimal(20)))
    await db.commit()

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    costed = (await client.get(f"/api/v1/recipes/{recipe.id}")).json()

    assert [(line["on_hand"], line["cost"]) for line in costed["lines"]] == [
        ("1000.000000", {"amount": "12.000000", "last_known": False}),
        ("0.000000", {"amount": "3.000000", "last_known": True}),
        ("40.000000", {"amount": None, "last_known": False}),
    ]
    assert costed["cost"] == {
        "labour": "10.000000",
        "total": "25.000000",
        "incomplete": True,
        "unit_cost": "0.026853",
    }
    doubled = (await client.get(f"/api/v1/recipes/{recipe.id}", params={"quantity": "1862"})).json()
    assert (doubled["cost"]["labour"], doubled["cost"]["total"]) == ("20.000000", "50.000000")

    await sign_in(client)
    hidden = (await client.get(f"/api/v1/recipes/{recipe.id}")).json()

    assert [(line["on_hand"], line["cost"]) for line in hidden["lines"]] == [
        ("1000.000000", None),
        ("0.000000", None),
        ("40.000000", None),
    ]
    assert hidden["cost"] is None
    listed = (await client.get("/api/v1/recipes", params={"base_id": alyx.id})).json()
    assert listed["items"][0]["cost"] is None
