"""The organization's settings."""

from __future__ import annotations

import pytest
from sqlmodel import select

from hoard.records import AdjustmentReason, DefaultReason, OrganizationSettings, Unit
from seneschal_core.db import WriteSession


async def test_settings_read_as_their_defaults_until_changed(client, sign_in, db: WriteSession):
    await sign_in(client)
    assert (await client.get("/api/v1/settings")).json() == {
        "currency": "USD",
        "auto_manufacture": "off",
    }

    db.add(OrganizationSettings(organization_id=db.organization_id, currency="EUR"))
    await db.commit()
    assert (await client.get("/api/v1/settings")).json()["currency"] == "EUR"

    # A change leaves what it does not send as it is.
    await sign_in(client, admin=True)
    changed = await client.put("/api/v1/settings", json={"auto_manufacture": "out_of_stock"})
    assert changed.json() == {"currency": "EUR", "auto_manufacture": "out_of_stock"}


#: A change to each kind of setting, as a method, path and body.
CHANGES = [
    ("PUT", "/api/v1/settings", {"currency": "EUR"}),
    ("POST", "/api/v1/settings/units", {"name": "kg"}),
    ("POST", "/api/v1/settings/categories", {"kind": "material", "name": "Tea"}),
    ("POST", "/api/v1/settings/locations", {"name": "Back room"}),
    ("POST", "/api/v1/settings/reasons", {"name": "Spilled"}),
]


@pytest.mark.parametrize(("method", "path", "body"), CHANGES)
async def test_only_an_admin_changes_the_settings(client, sign_in, method, path, body):
    await sign_in(client)
    refused = await client.request(method, path, json=body)
    assert (refused.status_code, refused.json()) == (403, {"detail": "ADMIN_ONLY"})

    await sign_in(client, admin=True)
    assert (await client.request(method, path, json=body)).status_code in (200, 201)


async def test_an_organization_starts_with_its_main_location_and_reasons_made_by_hoard(
    client, sign_in, db: WriteSession
):
    await sign_in(client)

    locations = (await client.get("/api/v1/settings/locations")).json()
    assert [(location["name"], location["main"]) for location in locations] == [(None, True)]
    reasons = (await client.get("/api/v1/settings/reasons")).json()
    assert [(reason["key"], reason["name"]) for reason in reasons] == [
        (key, None) for key in DefaultReason
    ]

    made = (await db.exec(select(AdjustmentReason))).all()
    assert {reason.created_by for reason in made} == {None}

    await sign_in(client, admin=True)
    lost = reasons[3]
    renamed = await client.patch(f"/api/v1/settings/reasons/{lost['id']}", json={"name": "Gone"})
    assert renamed.json() == {"id": lost["id"], "key": "lost", "name": "Gone"}
    reasons = (await client.get("/api/v1/settings/reasons")).json()
    assert [reason["name"] for reason in reasons[:2]] == ["Gone", None]


async def test_names_stay_unique_and_what_records_name_stays(
    client, sign_in, db: WriteSession, unit: Unit, material
):
    await sign_in(client, admin=True)
    await material("Oolong")
    await db.commit()

    taken = await client.post("/api/v1/settings/units", json={"name": unit.name})
    assert (taken.status_code, taken.json()) == (409, {"detail": "NAME_TAKEN"})
    in_use = await client.delete(f"/api/v1/settings/units/{unit.id}")
    assert (in_use.status_code, in_use.json()) == (409, {"detail": "IN_USE"})

    main = (await client.get("/api/v1/settings/locations")).json()[0]
    kept = await client.delete(f"/api/v1/settings/locations/{main['id']}")
    assert (kept.status_code, kept.json()) == (409, {"detail": "MAIN_LOCATION"})
    renamed = await client.patch(f"/api/v1/settings/locations/{main['id']}", json={"name": "Shop"})
    assert renamed.json() == {"id": main["id"], "name": "Shop", "main": True}
