"""How each kind of record is listed and shown on its page."""

from __future__ import annotations

LAYOUTS = "/api/v1/layouts"

TABLE = {"kind": "table", "definition": {"columns": ["name", "property:1"]}}
DETAIL = {
    "kind": "material",
    "definition": {
        "header": [{"type": "field", "props": {"field": "name"}}, {"type": "actions"}],
        "side": [{"type": "section", "children": [{"type": "properties"}]}],
    },
}


async def test_layouts_read_as_shipped_until_an_admin_changes_them(client, sign_in):
    await sign_in(client)
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
            "definition": {"header": None, "main": None, "side": None},
            "updated_at": None,
        },
    ]
    for body in (TABLE, DETAIL):
        refused = await client.put(f"{LAYOUTS}/material", json=body)
        assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})
    refused = await client.delete(f"{LAYOUTS}/material/table")
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    await client.put(f"{LAYOUTS}/material", json=TABLE)
    saved = (await client.put(f"{LAYOUTS}/material", json=DETAIL)).json()["layouts"]
    assert saved[0]["definition"]["columns"] == ["name", "property:1"]
    assert saved[1]["definition"]["header"] == DETAIL["definition"]["header"]
    assert all(layout["updated_at"] for layout in saved)
    assert (await client.get(f"{LAYOUTS}/product")).json()["layouts"][0]["updated_at"] is None

    reset = (await client.delete(f"{LAYOUTS}/material/table")).json()["layouts"]
    assert [layout["updated_at"] is None for layout in reset] == [True, False]


async def test_a_layout_hoard_does_not_draw_is_refused(client, sign_in):
    await sign_in(client, admin=True)
    for body in (
        {"kind": "board", "definition": {}},
        {"kind": "product", "definition": {}},
        {"kind": "table", "definition": {"header": []}},
    ):
        refused = await client.put(f"{LAYOUTS}/material", json=body)
        assert (refused.status_code, refused.json()) == (422, {"detail": "LAYOUT_UNKNOWN"}), body
    plugin_part = {"type": "plugin", "props": {"plugin": 1, "part": "x"}}
    refused = await client.put(
        f"{LAYOUTS}/material", json={"kind": "material", "definition": {"main": [plugin_part]}}
    )
    assert refused.status_code == 422
