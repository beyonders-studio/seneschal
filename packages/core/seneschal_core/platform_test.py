"""Initiative's calls to the plug-in platform's routes: the community's hooks,
and endpoints."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from seneschal_core import organizations
from seneschal_core.db import Database
from seneschal_core.organizations import Organization


async def _call(client, initiative_stand_in, hook: str, community_ref: str, body=None, **claims):
    token = initiative_stand_in.lifecycle(hook, community_ref=community_ref, **claims)
    return await client.post(
        f"/v1/hooks/{hook}", json=body or {}, headers={"Authorization": f"Bearer {token}"}
    )


async def _registered(database: Database, community_ref: str) -> Organization | None:
    async with database.session() as db:
        found = await db.execute(
            text("SELECT id FROM organizations WHERE community_ref = :ref"), {"ref": community_ref}
        )
        organization_id = found.scalar_one_or_none()
        return None if organization_id is None else await organizations.get(db, organization_id)


async def test_installed_registers_the_organization(
    client, initiative_stand_in, community_ref, serving_database
):
    answer = await _call(
        client, initiative_stand_in, "installed", community_ref, plugin_install_id=7
    )

    assert answer.status_code == 204
    organization = await _registered(serving_database, community_ref)
    assert organization is not None
    assert (organization.plugin_install_id, organization.community_status) == (7, "active")


async def test_installed_again_keeps_the_organization_and_records_the_new_install(
    client, initiative_stand_in, community_ref, serving_database
):
    await _call(client, initiative_stand_in, "installed", community_ref, plugin_install_id=7)
    first = await _registered(serving_database, community_ref)

    await _call(client, initiative_stand_in, "uninstalled", community_ref, plugin_install_id=7)
    await _call(client, initiative_stand_in, "installed", community_ref, plugin_install_id=8)

    again = await _registered(serving_database, community_ref)
    assert first is not None and again is not None
    assert (again.id, again.plugin_install_id) == (first.id, 8)


async def _with_widget(database: Database, community_ref: str) -> int:
    async with database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    async with database.organization_session(organization_id) as db:
        await db.execute(
            text("INSERT INTO widgets (organization_id, name) VALUES (:org, 'kept')"),
            {"org": organization_id},
        )
        await db.commit()
    return organization_id


async def _widget_count(database: Database, organization_id: int) -> int:
    async with database.organization_session(organization_id) as db:
        return (await db.execute(text("SELECT count(*) FROM widgets"))).scalar_one()


async def test_uninstalled_keeps_everything(
    client, initiative_stand_in, community_ref, serving_database
):
    organization_id = await _with_widget(serving_database, community_ref)

    answer = await _call(client, initiative_stand_in, "uninstalled", community_ref)

    assert answer.status_code == 204
    assert await _registered(serving_database, community_ref) is not None
    assert await _widget_count(serving_database, organization_id) == 1


async def test_erase_deletes_the_organization_and_its_rows(
    client, initiative_stand_in, community_ref, serving_database
):
    organization_id = await _with_widget(serving_database, community_ref)
    kept = await _with_widget(serving_database, f"{community_ref}-neighbour")

    answer = await _call(client, initiative_stand_in, "erase", community_ref)

    assert answer.status_code == 204
    assert await _registered(serving_database, community_ref) is None
    assert await _widget_count(serving_database, organization_id) == 0
    assert await _widget_count(serving_database, kept) == 1
    # Erasing a community nobody here knows leaves nothing behind.
    assert (await _call(client, initiative_stand_in, "erase", community_ref)).status_code == 204
    assert await _registered(serving_database, community_ref) is None


async def test_community_status_says_where_the_community_stands(
    client, initiative_stand_in, community_ref, serving_database
):
    answer = await _call(
        client, initiative_stand_in, "community_status", community_ref, {"status": "read_only"}
    )

    assert answer.status_code == 204
    organization = await _registered(serving_database, community_ref)
    assert organization is not None and organization.read_only


async def test_a_community_status_call_without_a_status_is_refused(
    client, initiative_stand_in, community_ref
):
    answer = await _call(
        client, initiative_stand_in, "community_status", community_ref, {"status": "paused"}
    )

    assert (answer.status_code, answer.json()) == (400, {"detail": "REQUEST_INVALID"})


async def test_entitlement_keeps_billings_newest_statement(
    client, initiative_stand_in, community_ref, serving_database
):
    def entitle(version: int, active: bool):
        statement = initiative_stand_in.statement(
            version,
            {"plan": f"plan-{version}", "active": active, "paid_through": "2026-12-01T00:00:00Z"},
        )
        return _call(
            client, initiative_stand_in, "entitlement", community_ref, {"statement": statement}
        )

    assert (await entitle(2, active=False)).status_code == 204
    assert (await entitle(1, active=True)).status_code == 204

    organization = await _registered(serving_database, community_ref)
    assert organization is not None
    assert (organization.statement_version, organization.plan, organization.active) == (
        2,
        "plan-2",
        False,
    )
    assert organization.read_only


async def test_a_statement_billing_did_not_sign_applies_nothing(
    client, initiative_stand_in, community_ref, serving_database
):
    statement = initiative_stand_in.statement(1, {"plan": "workshop"}, listing_uid="ANOTHERAPP0000")

    answer = await _call(
        client, initiative_stand_in, "entitlement", community_ref, {"statement": statement}
    )

    assert (answer.status_code, answer.json()) == (422, {"detail": "STATEMENT_REFUSED"})
    assert await _registered(serving_database, community_ref) is None


async def test_a_hook_the_app_does_not_handle_is_not_found(
    client, initiative_stand_in, community_ref
):
    answer = await _call(client, initiative_stand_in, "schedule", community_ref)

    assert (answer.status_code, answer.json()) == (404, {"detail": "HOOK_UNKNOWN"})


async def test_a_hook_needs_its_own_token(client, initiative_stand_in, community_ref):
    other_hook = initiative_stand_in.lifecycle("installed", community_ref=community_ref)
    handoff = initiative_stand_in.handoff(community_ref=community_ref)

    for headers in (
        {},
        {"Authorization": f"Bearer {other_hook}"},
        {"Authorization": f"Bearer {handoff}"},
    ):
        answer = await client.post("/v1/hooks/erase", json={}, headers=headers)
        assert (answer.status_code, answer.json()) == (401, {"detail": "INVALID_TOKEN"})


async def test_an_endpoint_call_names_the_endpoint_its_token_was_minted_for(
    client, initiative_stand_in, community_ref
):
    token = initiative_stand_in.context("plugin.core-test.stock", community_ref=community_ref)
    headers = {"Authorization": f"Bearer {token}"}

    mismatched = await client.post(
        "/v1/endpoints", json={"endpoint": "plugin.core-test.other"}, headers=headers
    )
    unknown = await client.post(
        "/v1/endpoints", json={"endpoint": "plugin.core-test.stock"}, headers=headers
    )

    assert (mismatched.status_code, mismatched.json()) == (400, {"detail": "REQUEST_INVALID"})
    assert (unknown.status_code, unknown.json()) == (400, {"detail": "ENDPOINT_UNKNOWN"})


async def test_a_body_too_large_is_refused_before_anything_else(client):
    answer = await client.post("/v1/hooks/installed", content=b"{" + b" " * (64 * 1024) + b"}")

    assert (answer.status_code, answer.json()) == (413, {"detail": "REQUEST_TOO_LARGE"})


#: The core's own endpoints (``conftest.py``): one only Initiative calls, and
#: one other plug-ins may call too.
WHO = "plugin.beyonders-studio.core-test.who"
PUBLIC_WHO = "plugin.beyonders-studio.core-test.public-who"


async def _endpoint(client, initiative_stand_in, endpoint: str, community_ref: str, **claims):
    token = initiative_stand_in.context(endpoint, community_ref=community_ref, **claims)
    return await client.post(
        "/v1/endpoints", json={"endpoint": endpoint}, headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.parametrize(
    ("claims", "who"),
    [
        ({}, {"caller": None, "actor": None}),
        ({"viewer": "member-3"}, {"caller": None, "actor": "member-3"}),
    ],
)
async def test_initiatives_own_call_acts_as_the_community_or_the_reader(
    client, initiative_stand_in, community_ref, claims, who
):
    for endpoint in (WHO, PUBLIC_WHO):
        answer = await _endpoint(client, initiative_stand_in, endpoint, community_ref, **claims)
        assert (answer.status_code, answer.json()["result"]) == (200, who)


async def test_another_plug_in_calls_only_a_public_endpoint_and_is_recorded(
    client, initiative_stand_in, community_ref
):
    for_member = {
        "act": {"sub": "beyonders-studio.automations"},
        "actor": "member",
        "member": "member-9",
    }

    refused = await _endpoint(client, initiative_stand_in, WHO, community_ref, **for_member)
    as_member = await _endpoint(
        client, initiative_stand_in, PUBLIC_WHO, community_ref, **for_member
    )
    as_community = await _endpoint(
        client,
        initiative_stand_in,
        PUBLIC_WHO,
        community_ref,
        act={"sub": "beyonders-studio.automations"},
        actor="installation",
    )

    assert (refused.status_code, refused.json()) == (401, {"detail": "INVALID_TOKEN"})
    assert as_member.json()["result"] == {
        "caller": "beyonders-studio.automations",
        "actor": "member-9",
    }
    assert as_community.json()["result"] == {
        "caller": "beyonders-studio.automations",
        "actor": None,
    }


async def test_a_community_that_may_not_change_is_refused_a_write(
    client, initiative_stand_in, community_ref
):
    answer = await _endpoint(
        client, initiative_stand_in, WHO, community_ref, community_status="read_only"
    )

    assert (answer.status_code, answer.json()) == (403, {"detail": "READ_ONLY"})
