"""A material's or a base's bin at each location, and its picture."""

from __future__ import annotations

from hoard.records import MAX_IMAGE_BYTES, Location, Product
from hoard.stock import add_product
from seneschal_core.db import WriteSession

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16


async def test_a_bin_is_set_at_a_location_and_cleared(
    client, sign_in, db: WriteSession, main: Location, material, lot
):
    back = Location(organization_id=db.organization_id, name="Back room")
    db.add(back)
    oolong, sencha = await material("Oolong"), await material("Sencha")
    await lot(oolong.id, "L1", "5")
    await db.commit()
    await sign_in(client)

    def bins(path: str) -> list[tuple[int, str, dict | None]]:
        return [(row["location_id"], row["quantity"], row["bin"]) for row in page[path]]

    shelf = {"zone": "A", "section": "2", "position": "Top"}
    for item in (oolong, sencha):
        put = await client.put(f"/api/v1/items/{item.id}/bins/{back.id}", json=shelf)
        assert put.status_code == 204
    page = (await client.get(f"/api/v1/materials/{oolong.id}")).json()
    assert bins("stock") == [(main.id, "5.000000", None), (back.id, "0", shelf)]
    page = (await client.get(f"/api/v1/materials/{sencha.id}")).json()
    assert bins("stock") == [(back.id, "0", shelf)]

    cleared = await client.put(f"/api/v1/items/{sencha.id}/bins/{back.id}", json={})
    assert cleared.status_code == 204
    page = (await client.get(f"/api/v1/materials/{sencha.id}")).json()
    assert page["stock"] == []
    unknown = await client.put(f"/api/v1/items/{oolong.id}/bins/{back.id + 99}", json=shelf)
    assert unknown.status_code == 404


async def test_a_material_or_a_base_keeps_one_picture(
    client, sign_in, db: WriteSession, material, base
):
    oolong, syrup = await material("Oolong"), await base("Syrup")
    _, latte = await add_product(db, Product(name="Latte"))
    await db.commit()
    await sign_in(client)
    picture = f"/api/v1/items/{oolong.id}/picture"

    refused = await client.put(
        picture, content=b"<svg/>", headers={"content-type": "image/svg+xml"}
    )
    assert (refused.status_code, refused.json()) == (415, {"detail": "IMAGE_TYPE"})
    large = PNG + b"\x00" * MAX_IMAGE_BYTES
    refused = await client.put(picture, content=large, headers={"content-type": "image/png"})
    assert (refused.status_code, refused.json()) == (413, {"detail": "IMAGE_TOO_LARGE"})
    variation = await client.put(
        f"/api/v1/items/{latte.id}/picture", content=PNG, headers={"content-type": "image/png"}
    )
    assert variation.status_code == 404

    first = (await client.put(picture, content=PNG, headers={"content-type": "image/png"})).json()
    second = (await client.put(picture, content=GIF, headers={"content-type": "image/gif"})).json()
    assert (second["content_type"], second["size"]) == ("image/gif", len(GIF))
    page = (await client.get(f"/api/v1/materials/{oolong.id}")).json()
    assert page["picture_id"] == second["id"]
    shown = await client.get(f"{picture}/{second['id']}")
    assert (shown.content, shown.headers["content-type"]) == (GIF, "image/gif")
    assert (await client.get(f"{picture}/{first['id']}")).status_code == 404
    elsewhere = await client.get(f"/api/v1/items/{syrup.id}/picture/{second['id']}")
    assert elsewhere.status_code == 404

    based = await client.put(
        f"/api/v1/items/{syrup.id}/picture", content=PNG, headers={"content-type": "image/png"}
    )
    assert based.status_code == 200
    assert (await client.get(f"/api/v1/bases/{syrup.id}")).json()["picture_id"] == based.json()[
        "id"
    ]

    assert (await client.delete(picture)).status_code == 204
    assert (await client.get(f"/api/v1/materials/{oolong.id}")).json()["picture_id"] is None
