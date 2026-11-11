"""The organization's settings."""

from __future__ import annotations

from hoard.records import OrganizationSettings
from seneschal_core.db import WriteSession


async def test_settings_read_as_their_defaults_until_changed(client, sign_in, db: WriteSession):
    await sign_in(client)
    assert (await client.get("/api/v1/settings")).json() == {"currency": "USD"}

    db.add(OrganizationSettings(organization_id=db.organization_id, currency="EUR"))
    await db.commit()

    assert (await client.get("/api/v1/settings")).json() == {"currency": "EUR"}
