"""Recipes are added, read scaled with their shares by weight, listed by what
they make, edited and removed; a bundle's holds variations."""

from __future__ import annotations

from hoard.records import Product
from hoard.stock import add_product
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
            "lines": [
                {"item_id": tea.id, "quantity": "300"},
                {"item_id": syrup.id, "quantity": "200"},
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
        json={"base_id": syrup.id, "lines": [{"item_id": spice.id, "quantity": "1"}]},
    )
    assert (cycle.status_code, cycle.json()) == (422, {"detail": "RECIPE_CYCLE"})
    product = await client.post(
        "/api/v1/recipes",
        json={"variation_id": latte_one.id, "lines": [{"item_id": latte_one.id, "quantity": "1"}]},
    )
    assert (product.status_code, product.json()) == (422, {"detail": "RECIPE_LINE_KIND"})
    await client.post(
        "/api/v1/recipes",
        json={"variation_id": latte_one.id, "lines": [{"item_id": spice.id, "quantity": "5"}]},
    )

    edited = await client.put(
        f"/api/v1/recipes/{recipe['id']}",
        json={"batch_quantity": "250", "lines": [{"item_id": tea.id, "quantity": "250"}]},
    )
    assert [line["quantity"] for line in edited.json()["lines"]] == ["250.000000"]
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
        json={"product_id": box.id, "lines": [{"item_id": tea.id, "quantity": "1"}]},
    )
    made = await client.post(
        "/api/v1/recipes",
        json={"product_id": box.id, "lines": [{"item_id": tin.id, "quantity": "3"}]},
    )

    assert (refused.status_code, refused.json()) == (422, {"detail": "RECIPE_LINE_KIND"})
    bundle = made.json()
    assert (bundle["output"], bundle["name"], bundle["unit_id"]) == ("bundle", "Tea box", None)
    assert [(line["item"]["kind"], line["percent"]) for line in bundle["lines"]] == [
        ("variation", None)
    ]
    await db.refresh(box)
    assert box.bundle
