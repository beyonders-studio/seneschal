"""GTINs: the barcode number on a material, a base or a variation."""

from __future__ import annotations

from hoard.records import Unit
from seneschal_core.db import WriteSession

#: A UPC-A, and the same as the 14 digits Hoard keeps.
UPC = "012345678905"
GTIN = "00012345678905"
#: GS1's example GTIN.
OTHER = "09506000134352"


async def test_a_material_keeps_its_gtin_as_fourteen_digits_and_search_finds_it(
    client, sign_in, db: WriteSession, unit: Unit
):
    await db.commit()
    await sign_in(client)

    made = await client.post(
        "/api/v1/materials", json={"name": "Sencha", "tracking_unit_id": unit.id, "gtin": UPC}
    )

    assert (made.status_code, made.json()["gtin"]) == (201, GTIN)
    found = (await client.get("/api/v1/search/suggest", params={"q": GTIN})).json()
    assert found["exact_match"] is True
    assert found["items"][0]["title"] == "Sencha"


async def test_a_number_that_isnt_a_gtin_is_refused(client, sign_in, db: WriteSession, unit: Unit):
    await db.commit()
    await sign_in(client)

    for wrong in ("012345678900", "12345", "0123456789AB"):
        refused = await client.post(
            "/api/v1/materials",
            json={"name": "Sencha", "tracking_unit_id": unit.id, "gtin": wrong},
        )
        assert (refused.status_code, refused.json()) == (422, {"detail": "GTIN_INVALID"}), wrong


async def test_a_gtin_belongs_to_one_material_base_or_variation(
    client, sign_in, db: WriteSession, unit: Unit, material, base, product
):
    held = await material("Sencha", gtin=GTIN)
    variation = await product("Tea tin")
    await base("Syrup")
    await db.commit()
    await sign_in(client)

    as_base = await client.post(
        "/api/v1/bases", json={"name": "Blend", "tracking_unit_id": unit.id, "gtin": UPC}
    )
    as_variation = await client.put(
        f"/api/v1/products/{variation.product_id}/variations/{variation.id}", json={"gtin": UPC}
    )
    again = await client.put(
        f"/api/v1/materials/{held.id}",
        json={"name": "Sencha", "tracking_unit_id": unit.id, "gtin": GTIN},
    )

    assert (as_base.status_code, as_base.json()) == (409, {"detail": "GTIN_TAKEN"})
    assert (as_variation.status_code, as_variation.json()) == (409, {"detail": "GTIN_TAKEN"})
    assert (again.status_code, again.json()["gtin"]) == (200, GTIN)


async def test_a_write_that_doesnt_send_a_gtin_leaves_it_and_one_that_sends_null_clears_it(
    client, sign_in, db: WriteSession, unit: Unit, material, product
):
    held = await material("Sencha", gtin=GTIN)
    variation = await product("Tea tin")
    await db.commit()
    await sign_in(client)
    path = f"/api/v1/products/{variation.product_id}/variations/{variation.id}"
    await client.put(path, json={"gtin": OTHER})

    kept = await client.put(
        f"/api/v1/materials/{held.id}", json={"name": "Green", "tracking_unit_id": unit.id}
    )
    renamed = await client.put(path, json={"sku": "TIN-1"})
    cleared = await client.put(
        f"/api/v1/materials/{held.id}",
        json={"name": "Green", "tracking_unit_id": unit.id, "gtin": None},
    )

    assert kept.json()["gtin"] == GTIN
    variations = renamed.json()["variations"]
    assert (variations[0]["sku"], variations[0]["gtin"]) == ("TIN-1", OTHER)
    assert cleared.json()["gtin"] is None


async def test_a_base_and_a_variation_keep_and_show_their_gtins(
    client, sign_in, db: WriteSession, unit: Unit, product
):
    variation = await product("Tea tin")
    await db.commit()
    await sign_in(client)

    made = await client.post(
        "/api/v1/bases", json={"name": "Blend", "tracking_unit_id": unit.id, "gtin": OTHER}
    )
    page = await client.put(
        f"/api/v1/products/{variation.product_id}/variations/{variation.id}", json={"gtin": UPC}
    )

    assert made.json()["gtin"] == OTHER
    assert page.json()["variations"][0]["gtin"] == GTIN
    found = (await client.get("/api/v1/search/suggest", params={"q": UPC})).json()
    assert found["items"] == []
    found = (await client.get("/api/v1/search/suggest", params={"q": GTIN})).json()
    assert [hit["title"] for hit in found["items"]] == ["Tea tin"]
