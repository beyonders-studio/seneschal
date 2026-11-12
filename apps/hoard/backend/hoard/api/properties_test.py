"""The properties people add to records, and each record's values of them."""

from __future__ import annotations

import pytest

from hoard.records import PropertyDefinition, PropertyType, RecordKind
from seneschal_core.db import WriteSession

PROPERTIES = "/api/v1/properties"


async def _defined(db: WriteSession, **fields) -> PropertyDefinition:
    definition = PropertyDefinition(organization_id=db.organization_id, **fields)
    db.add(definition)
    await db.commit()
    return definition


async def test_only_an_admin_changes_the_properties(client, sign_in, db: WriteSession):
    colour = await _defined(
        db,
        kind=RecordKind.MATERIAL,
        name="Colour",
        type=PropertyType.SELECT,
        options=[{"value": "red", "label": "Red"}],
    )
    changes = [
        ("POST", PROPERTIES, {"kind": "material", "name": "Origin", "type": "text"}),
        ("PATCH", f"{PROPERTIES}/{colour.id}", {"name": "Shade"}),
        ("POST", f"{PROPERTIES}/{colour.id}/options", {"label": "Blue"}),
        ("DELETE", f"{PROPERTIES}/{colour.id}", None),
    ]
    await sign_in(client)
    for method, path, body in changes:
        refused = await client.request(method, path, json=body)
        assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"}), path

    await sign_in(client, admin=True)
    for method, path, body in changes:
        assert (await client.request(method, path, json=body)).status_code in (200, 201, 204)


async def test_a_property_takes_what_its_type_holds(client, sign_in):
    await sign_in(client, admin=True)

    no_options = {"kind": "lot", "name": "Grade", "type": "select", "options": []}
    refused = await client.post(PROPERTIES, json=no_options)
    assert (refused.status_code, refused.json()) == (422, {"detail": "PROPERTY_INVALID"})

    price = await client.post(
        PROPERTIES,
        json={"kind": "lot", "name": "Price", "type": "currency", "currency": "EUR", "options": []},
    )
    assert price.json() | {"id": 0} == {
        "id": 0,
        "kind": "lot",
        "name": "Price",
        "type": "currency",
        "options": None,
        "currency": "EUR",
        "counter": None,
    }
    taken = await client.post(PROPERTIES, json={"kind": "lot", "name": "Price", "type": "text"})
    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    listed = (await client.get(PROPERTIES, params={"kind": "lot"})).json()
    assert [each["name"] for each in listed] == ["Price"]
    assert (await client.get(PROPERTIES, params={"kind": "product"})).json() == []


async def test_a_value_round_trips_on_a_material(client, sign_in, material, db: WriteSession):
    flour = await material("Flour")
    origin = await _defined(db, kind=RecordKind.MATERIAL, name="Origin", type=PropertyType.TEXT)
    stacks = await _defined(db, kind=RecordKind.MATERIAL, name="Stacks", type=PropertyType.COUNTER)
    await sign_in(client)

    values = f"{PROPERTIES}/values/material/{flour.id}"
    answer = await client.put(
        values,
        json={
            "values": [{"property_id": origin.id, "value": "Kansas"}],
            "steps": [{"property_id": stacks.id, "up": True}],
        },
    )
    assert answer.status_code == 200, answer.text
    assert [(each["name"], each["value"]) for each in answer.json()] == [
        ("Origin", "Kansas"),
        ("Stacks", 1),
    ]

    read = (await client.get(f"/api/v1/materials/{flour.id}")).json()
    assert [(each["property_id"], each["value"]) for each in read["properties"]] == [
        (origin.id, "Kansas"),
        (stacks.id, 1),
    ]
    listed = (await client.get("/api/v1/materials")).json()["items"]
    assert [each["value"] for each in listed[0]["properties"]] == ["Kansas", 1]

    cleared = await client.put(values, json={"removed": [stacks.id]})
    assert [each["name"] for each in cleared.json()] == ["Origin"]


@pytest.mark.parametrize(
    ("kind", "value"), [(PropertyType.NUMBER, "many"), (PropertyType.SELECT, "blue")]
)
async def test_a_value_its_property_cannot_hold_is_refused(
    client, sign_in, material, db: WriteSession, kind, value
):
    flour = await material("Flour")
    definition = await _defined(
        db,
        kind=RecordKind.MATERIAL,
        name="Weight",
        type=kind,
        options=[{"value": "red", "label": "Red"}] if kind is PropertyType.SELECT else None,
    )
    lot_property = await _defined(db, kind=RecordKind.LOT, name="Grade", type=PropertyType.TEXT)
    await sign_in(client)

    values = f"{PROPERTIES}/values/material/{flour.id}"
    refused = await client.put(
        values, json={"values": [{"property_id": definition.id, "value": value}]}
    )
    assert (refused.status_code, refused.json()) == (422, {"detail": "VALUE_INVALID"})
    other_kind = await client.put(
        values, json={"values": [{"property_id": lot_property.id, "value": "A"}]}
    )
    assert (other_kind.status_code, other_kind.json()) == (422, {"detail": "PROPERTY_UNKNOWN"})


async def test_values_are_refused_while_hoard_is_read_only(
    client, sign_in, material, db: WriteSession, initiative_stand_in, community_ref
):
    flour = await material("Flour")
    origin = await _defined(db, kind=RecordKind.MATERIAL, name="Origin", type=PropertyType.TEXT)
    await sign_in(client)
    token = initiative_stand_in.lifecycle("community_status", community_ref=community_ref)
    told = await client.post(
        "/v1/hooks/community_status",
        json={"status": "read_only"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert told.status_code == 204

    refused = await client.put(
        f"{PROPERTIES}/values/material/{flour.id}",
        json={"values": [{"property_id": origin.id, "value": "Kansas"}]},
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "READ_ONLY"})
