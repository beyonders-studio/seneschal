"""A page's sign-in: the handoff exchanged once for the app's session."""

from __future__ import annotations

from sqlalchemy import text

from seneschal_core import organizations
from seneschal_core.db import Database
from seneschal_core.testing import InitiativeStandIn

EXCHANGE = "/api/v1/auth/handoff/exchange"
ME = "/api/v1/auth/me"


async def test_a_handoff_is_exchanged_for_a_session(client, initiative_stand_in, community_ref):
    token = initiative_stand_in.handoff(
        community_ref=community_ref,
        member_ref="member-3",
        page="production",
        initiative_id=9,
        permissions=("use",),
    )

    exchanged = await client.post(
        EXCHANGE, json={"handoff_token": token, "theme": "dark", "locale": "fr-CA"}
    )

    assert exchanged.status_code == 200
    me = {
        "member_ref": "member-3",
        "community_ref": community_ref,
        "initiative_id": 9,
        "page": "production",
        "admin": False,
        "permissions": ["use"],
        "theme": "dark",
        "locale": "fr-CA",
        "read_only": False,
        "plan": None,
    }
    assert exchanged.json() == me
    cookie = exchanged.headers["set-cookie"]
    assert cookie.startswith("core_session=") and "HttpOnly" in cookie

    assert (await client.get(ME)).json() == me


async def test_a_handoff_is_exchanged_once(client, initiative_stand_in, community_ref):
    token = initiative_stand_in.handoff(community_ref=community_ref)

    assert (await client.post(EXCHANGE, json={"handoff_token": token})).status_code == 200
    replayed = await client.post(EXCHANGE, json={"handoff_token": token})

    assert replayed.status_code == 409
    assert replayed.json() == {"detail": "HANDOFF_ALREADY_REDEEMED"}


async def test_a_token_initiative_did_not_sign_is_refused(
    client, initiative_stand_in, community_ref
):
    other = InitiativeStandIn(
        public_id=initiative_stand_in.public_id, listing_uid=initiative_stand_in.listing_uid
    )

    refused = await client.post(
        EXCHANGE, json={"handoff_token": other.handoff(community_ref=community_ref)}
    )

    assert (refused.status_code, refused.json()) == (401, {"detail": "INVALID_HANDOFF_TOKEN"})
    assert "set-cookie" not in refused.headers


async def test_a_community_page_opens_for_admins_alone(client, initiative_stand_in, community_ref):
    def settings(admin: bool) -> str:
        return initiative_stand_in.handoff(
            community_ref=community_ref, page="settings", initiative_id=None, admin=admin
        )

    member = await client.post(EXCHANGE, json={"handoff_token": settings(admin=False)})
    admin = await client.post(EXCHANGE, json={"handoff_token": settings(admin=True)})

    assert (member.status_code, member.json()) == (403, {"detail": "COMMUNITY_PAGE_ADMIN_ONLY"})
    assert admin.status_code == 200
    assert (admin.json()["initiative_id"], admin.json()["admin"]) == (None, True)


async def test_a_suspended_community_gets_no_session(client, initiative_stand_in, community_ref):
    token = initiative_stand_in.handoff(community_ref=community_ref, community_status="suspended")

    refused = await client.post(EXCHANGE, json={"handoff_token": token})

    assert (refused.status_code, refused.json()) == (403, {"detail": "COMMUNITY_UNAVAILABLE"})


async def test_the_handoff_catches_the_registry_up_with_where_the_community_stands(
    client, initiative_stand_in, community_ref
):
    token = initiative_stand_in.handoff(community_ref=community_ref, community_status="on_hold")

    exchanged = await client.post(EXCHANGE, json={"handoff_token": token})

    assert exchanged.json()["read_only"] is True


async def test_a_page_without_a_session_is_not_signed_in(client):
    refused = await client.get(ME)

    assert (refused.status_code, refused.json()) == (401, {"detail": "NOT_AUTHENTICATED"})


async def test_a_session_the_app_did_not_sign_is_refused(
    client, initiative_stand_in, community_ref
):
    token = initiative_stand_in.handoff(community_ref=community_ref)
    await client.post(EXCHANGE, json={"handoff_token": token})
    session = client.cookies["core_session"]
    client.cookies.clear()
    client.cookies.set("core_session", session)
    assert (await client.get(ME)).status_code == 200

    header, payload, _ = session.split(".")
    client.cookies.set("core_session", f"{header}.{payload}.{'A' * 86}")

    assert (await client.get(ME)).status_code == 401


async def test_an_erased_organizations_session_ends(
    client, initiative_stand_in, community_ref, serving_database: Database
):
    token = initiative_stand_in.handoff(community_ref=community_ref)
    await client.post(EXCHANGE, json={"handoff_token": token})

    async with serving_database.session() as db:
        assert await organizations.erase(db, community_ref)
        await db.commit()

    assert (await client.get(ME)).status_code == 401


async def test_a_spent_handoff_is_kept_under_its_organization(
    client, initiative_stand_in, community_ref, serving_database: Database
):
    await client.post(
        EXCHANGE, json={"handoff_token": initiative_stand_in.handoff(community_ref=community_ref)}
    )
    async with serving_database.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )

    async with serving_database.organization_session(organization_id) as db:
        spent = (await db.execute(text("SELECT count(*) FROM handoff_redemptions"))).scalar_one()

    assert spent == 1


async def test_a_locale_is_a_language_tag(client, initiative_stand_in, community_ref):
    token = initiative_stand_in.handoff(community_ref=community_ref)

    refused = await client.post(EXCHANGE, json={"handoff_token": token, "locale": "en<script>"})

    assert refused.status_code == 422
