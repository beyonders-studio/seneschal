"""Products, their variations and images, and their stock."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import plugin_manifest
from hoard.records import MAX_IMAGE_BYTES, Adjustment, AdjustmentKind, Location, Product
from hoard.stock import add_product, adjust
from seneschal_core.db import WriteSession

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


async def _product(db: WriteSession, main: Location, on_hand: str) -> Product:
    product, default = await add_product(
        db, Product(name="Chai", default_price=Decimal(12), low_stock_limit=Decimal(2))
    )
    await adjust(
        db,
        Adjustment(
            item_id=default.id,
            location_id=main.id,
            kind=AdjustmentKind.STARTING,
            dated_on=date(2026, 11, 1),
            change=Decimal(on_hand),
            unit_cost=Decimal(3),
        ),
    )
    await db.commit()
    return product


async def test_a_product_page_holds_its_variations_stock_and_prices_only_for_costs(
    client, sign_in, db: WriteSession, main: Location
):
    product = await _product(db, main, "4")
    await sign_in(client)

    added = await client.post(
        f"/api/v1/products/{product.id}/variations",
        json={"attributes": {"size": "large"}, "price": "15"},
    )
    page = added.json()
    assert (added.status_code, page["on_hand"], page["low_stock_limit"]) == (
        201,
        "4.000000",
        "2.000000",
    )
    assert [(v["attributes"], v["is_default"], v["on_hand"]) for v in page["variations"]] == [
        ({}, True, "4.000000"),
        ({"size": "large"}, False, "0"),
    ]
    assert page["variations"][0]["stock"] == [{"location_id": main.id, "quantity": "4.000000"}]
    hidden = [page["default_price"]] + [
        v[field] for v in page["variations"] for field in ("price", "average_cost")
    ]
    assert hidden == [None] * 5

    await sign_in(client, (plugin_manifest.SEE_COSTS,))
    page = (await client.get(f"/api/v1/products/{product.id}")).json()
    assert page["default_price"] == "12.000000"
    assert [(v["price"], v["average_cost"]) for v in page["variations"]] == [
        (None, "3.000000"),
        (None, "0.000000"),
    ]
    listed = (await client.get("/api/v1/products")).json()
    assert [(p["name"], p["on_hand"]) for p in listed["items"]] == [("Chai", "4.000000")]


async def test_a_product_keeps_its_last_variation(client, sign_in, db: WriteSession, main):
    product = await _product(db, main, "1")
    await sign_in(client)
    page = (await client.get(f"/api/v1/products/{product.id}")).json()

    last = await client.delete(
        f"/api/v1/products/{product.id}/variations/{page['variations'][0]['id']}"
    )
    assert (last.status_code, last.json()) == (409, {"detail": "LAST_VARIATION"})
    assert (await client.delete(f"/api/v1/products/{product.id}")).status_code == 204
    assert (await client.get(f"/api/v1/products/{product.id}")).status_code == 404


async def test_auto_manufacture_is_changed_only_when_sent(client, sign_in, db: WriteSession, main):
    product = await _product(db, main, "1")
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"
    variation = (await client.get(path)).json()["variations"][0]["id"]

    always = await client.put(path, json={"name": "Chai", "auto_manufacture": "always"})
    renamed = await client.put(path, json={"name": "Masala chai"})
    made = await client.put(
        f"{path}/variations/{variation}", json={"auto_manufacture": "out_of_stock"}
    )

    assert always.json()["auto_manufacture"] == renamed.json()["auto_manufacture"] == "always"
    assert made.json()["variations"][0]["auto_manufacture"] == "out_of_stock"


async def test_a_product_keeps_its_cure_time_and_its_traceability_flags(
    client, sign_in, db: WriteSession, main
):
    product = await _product(db, main, "1")
    await sign_in(client)
    path = f"/api/v1/products/{product.id}"

    kept = await client.put(
        path,
        json={"name": "Soap", "cure_days": 28, "traceability_list": True, "rinse_off": True},
    )
    refused = await client.put(path, json={"name": "Soap", "cure_days": 0})

    page = kept.json()
    assert (page["cure_days"], page["traceability_list"], page["rinse_off"]) == (28, True, True)
    assert refused.status_code == 422


async def test_product_images_are_kept_by_type_and_size(
    client, sign_in, db: WriteSession, main: Location
):
    product = await _product(db, main, "1")
    images = f"/api/v1/products/{product.id}/images"
    await sign_in(client)

    for content_type, content in (
        ("image/svg+xml", b"<svg/>"),
        ("image/png", b"GIF89a"),
        ("text/html", PNG),
    ):
        refused = await client.post(images, content=content, headers={"content-type": content_type})
        assert (refused.status_code, refused.json()) == (415, {"detail": "IMAGE_TYPE"})
    large = PNG + b"\x00" * MAX_IMAGE_BYTES
    refused = await client.post(images, content=large, headers={"content-type": "image/png"})
    assert (refused.status_code, refused.json()) == (413, {"detail": "IMAGE_TOO_LARGE"})

    added = await client.post(images, content=PNG, headers={"content-type": "image/png"})
    assert added.status_code == 201
    image = added.json()
    assert (image["position"], image["size"], image["created_by"]) == (0, len(PNG), "member-ref")
    assert [listed["id"] for listed in (await client.get(images)).json()] == [image["id"]]
    shown = await client.get(f"{images}/{image['id']}")
    assert (shown.content, shown.headers["content-type"]) == (PNG, "image/png")

    assert (await client.delete(f"{images}/{image['id']}")).status_code == 204
    assert (await client.get(images)).json() == []
