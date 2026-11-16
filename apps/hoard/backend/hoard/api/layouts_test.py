"""How each kind of record is listed and shown on its page."""

from __future__ import annotations

LAYOUTS = "/api/v1/layouts"
TYPES = "/api/v1/settings/types"

TABLE = {"kind": "table", "definition": {"columns": ["name", "property:1"]}}
DETAIL = {
    "kind": "material",
    "definition": {
        "header": [{"type": "field", "props": {"field": "name"}}, {"type": "actions"}],
        "side": [{"type": "section", "children": [{"type": "properties"}]}],
    },
}


async def _default_type(client, kind: str) -> int:
    return next(
        each["id"]
        for each in (await client.get(TYPES, params={"kind": kind})).json()
        if each["is_default"]
    )


async def test_layouts_read_as_shipped_until_an_admin_changes_them(client, sign_in):
    await sign_in(client)
    default = await _default_type(client, "material")
    detail = DETAIL | {"type_id": default}
    shipped = (await client.get(f"{LAYOUTS}/material")).json()["layouts"]
    assert shipped == [
        {
            "kind": "table",
            "is_default": True,
            "hidden": False,
            "definition": {"columns": None, "displays": None},
            "updated_at": None,
        },
        {
            "kind": "material",
            "type_id": default,
            "definition": {"header": None, "main": None, "side": None},
            "updated_at": None,
        },
    ]
    for body in (TABLE, detail):
        refused = await client.put(f"{LAYOUTS}/material", json=body)
        assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})
    refused = await client.delete(f"{LAYOUTS}/material/table")
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    await client.put(f"{LAYOUTS}/material", json=TABLE)
    saved = (await client.put(f"{LAYOUTS}/material", json=detail)).json()["layouts"]
    assert saved[0]["definition"]["columns"] == ["name", "property:1"]
    assert saved[1]["definition"]["header"] == DETAIL["definition"]["header"]
    assert all(layout["updated_at"] for layout in saved)
    assert (await client.get(f"{LAYOUTS}/product")).json()["layouts"][0]["updated_at"] is None

    reset = (await client.delete(f"{LAYOUTS}/material/table")).json()["layouts"]
    assert [layout["updated_at"] is None for layout in reset] == [True, False]


async def test_each_type_has_its_own_page_which_goes_with_it(client, sign_in):
    await sign_in(client, admin=True)
    default = await _default_type(client, "material")
    packaging = (
        await client.post(TYPES, json={"kind": "material", "name": "Tins", "shape": "packaging"})
    ).json()["id"]
    pages = (await client.get(f"{LAYOUTS}/material")).json()["layouts"][1:]
    assert [page["type_id"] for page in pages] == [default, packaging]

    saved = (await client.put(f"{LAYOUTS}/material", json=DETAIL | {"type_id": packaging})).json()[
        "layouts"
    ]
    assert [page["updated_at"] is None for page in saved[1:]] == [True, False]
    reset = await client.delete(f"{LAYOUTS}/material/material", params={"type_id": default})
    assert [page["updated_at"] is None for page in reset.json()["layouts"][1:]] == [True, False]
    reset = await client.delete(f"{LAYOUTS}/material/material", params={"type_id": packaging})
    assert all(page["updated_at"] is None for page in reset.json()["layouts"])

    # A page laid out goes with its type.
    await client.put(f"{LAYOUTS}/material", json=DETAIL | {"type_id": packaging})
    assert (await client.delete(f"{TYPES}/{packaging}")).status_code == 204
    pages = (await client.get(f"{LAYOUTS}/material")).json()["layouts"][1:]
    assert [page["type_id"] for page in pages] == [default]

    # A kind not laid out per type keeps one page.
    lot = (await client.get(f"{LAYOUTS}/lot")).json()["layouts"]
    assert [page.get("type_id") for page in lot] == [None, None]


async def test_a_layout_hoard_does_not_draw_is_refused(client, sign_in):
    await sign_in(client, admin=True)
    material = await _default_type(client, "material")
    product = await _default_type(client, "product")
    for kind, body in (
        ("material", {"kind": "board", "definition": {}}),
        ("material", {"kind": "product", "definition": {}}),
        ("material", {"kind": "table", "definition": {"header": []}}),
        # A material's page is its type's, and a lot's is not laid out per type.
        ("material", {"kind": "material", "definition": {}}),
        ("lot", {"kind": "lot", "type_id": material, "definition": {}}),
    ):
        refused = await client.put(f"{LAYOUTS}/{kind}", json=body)
        assert (refused.status_code, refused.json()) == (422, {"detail": "LAYOUT_UNKNOWN"}), body
    refused = await client.put(
        f"{LAYOUTS}/material", json={"kind": "material", "type_id": product, "definition": {}}
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "TYPE_UNKNOWN"})
    refused = await client.delete(f"{LAYOUTS}/material/table", params={"type_id": material})
    assert (refused.status_code, refused.json()) == (422, {"detail": "LAYOUT_UNKNOWN"})
    plugin_part = {"type": "plugin", "props": {"plugin": 1, "part": "x"}}
    refused = await client.put(
        f"{LAYOUTS}/material", json={"kind": "material", "definition": {"main": [plugin_part]}}
    )
    assert refused.status_code == 422
