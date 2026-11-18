"""Sign in with Initiative at the app's own address: the flow, the renewal
spent once, and how a sign-in ends."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx2 import URL, AsyncClient, Response

from seneschal_core.sign_in import Sealer, Unsealable
from seneschal_core.testing import InitiativeStandIn

START = "/api/v1/auth/sign-in/start"
RETURN = "/api/v1/auth/sign-in/return"
REFRESH = "/api/v1/auth/refresh"
SIGN_OUT = "/api/v1/auth/sign-out"
ME = "/api/v1/auth/me"
#: The test app's origin (conftest's APP_ORIGIN).
ORIGIN = "http://test"


async def sign_in(
    client: AsyncClient,
    initiative: InitiativeStandIn,
    community_ref: str,
    *,
    next: str = "/production",
    **place: Any,
) -> Response:
    """Start a sign-in, choose a place at Initiative, and come back."""
    started = await client.get(START, params={"next": next})
    assert started.status_code == 303
    back = initiative.authorize(started.headers["location"], community_ref=community_ref, **place)
    assert back.startswith(f"{ORIGIN}{RETURN}?")
    return await client.get(back.removeprefix(ORIGIN))


def _renewal(client: AsyncClient) -> str:
    return client.cookies["core_renew"]


def _present(client: AsyncClient, renewal: str) -> None:
    """Hold only ``renewal``, as a browser that kept an old one would."""
    client.cookies.clear()
    client.cookies.set("core_renew", renewal)


async def test_a_person_signs_in_and_lands_where_they_were_going(
    client, initiative_stand_in, community_ref
):
    started = await client.get(START, params={"next": "/production"})

    location = started.headers["location"]
    assert location.startswith(f"{InitiativeStandIn.ORIGIN}/oauth/authorize?")
    assert URL(location).params["redirect_uri"] == f"{ORIGIN}{RETURN}"
    assert started.headers["cache-control"] == "no-store"

    back = initiative_stand_in.authorize(
        location, community_ref=community_ref, member_ref="member-5", initiative_id=4
    )
    landed = await client.get(back.removeprefix(ORIGIN))

    assert (landed.status_code, landed.headers["location"]) == (303, "/production")
    assert {"core_session", "core_renew"} <= set(client.cookies.keys())
    me = (await client.get(ME)).json()
    assert (me["member_ref"], me["community_ref"], me["initiative_id"], me["page"]) == (
        "member-5",
        community_ref,
        4,
        "stock",
    )


async def test_the_session_cookie_of_a_sign_in_is_lax_and_the_renewal_stays_in_auth(
    client, initiative_stand_in, community_ref
):
    landed = await sign_in(client, initiative_stand_in, community_ref)

    cookies = landed.headers.get_list("set-cookie")
    session = next(cookie for cookie in cookies if cookie.startswith("core_session="))
    renewal = next(cookie for cookie in cookies if cookie.startswith("core_renew="))
    assert "SameSite=lax" in session and "HttpOnly" in session
    assert "Path=/api/v1/auth" in renewal and "HttpOnly" in renewal


async def test_a_renewal_is_spent_for_a_new_session_and_a_new_renewal(
    client, initiative_stand_in, community_ref
):
    await sign_in(client, initiative_stand_in, community_ref, member_ref="member-6")
    first = _renewal(client)

    renewed = await client.post(REFRESH)

    assert renewed.status_code == 200
    assert renewed.json()["member_ref"] == "member-6"
    assert renewed.headers["cache-control"] == "no-store"
    assert _renewal(client) != first
    assert (await client.post(REFRESH)).status_code == 200


async def test_a_renewal_presented_again_ends_the_whole_sign_in(
    client, initiative_stand_in, community_ref
):
    await sign_in(client, initiative_stand_in, community_ref)
    stolen = _renewal(client)
    await client.post(REFRESH)
    current = _renewal(client)

    _present(client, stolen)
    replayed = await client.post(REFRESH)

    assert (replayed.status_code, replayed.json()) == (401, {"detail": "SIGN_IN_ENDED"})
    cleared = replayed.headers.get_list("set-cookie")
    assert any(cookie.startswith('core_renew=""') for cookie in cleared)
    # The person's own renewal ended with the thief's.
    _present(client, current)
    assert (await client.post(REFRESH)).status_code == 401


async def test_signing_out_of_initiative_ends_the_sign_in_at_its_next_renewal(
    client, initiative_stand_in, community_ref
):
    await sign_in(client, initiative_stand_in, community_ref)

    initiative_stand_in.end_sign_in()
    refused = await client.post(REFRESH)

    assert (refused.status_code, refused.json()) == (401, {"detail": "SIGN_IN_ENDED"})


async def test_signing_out_ends_the_sign_in_and_clears_its_cookies(
    client, initiative_stand_in, community_ref
):
    await sign_in(client, initiative_stand_in, community_ref)
    renewal = _renewal(client)

    signed_out = await client.post(SIGN_OUT)

    assert signed_out.status_code == 204
    _present(client, renewal)
    assert (await client.post(REFRESH)).status_code == 401


async def test_a_renewal_the_app_did_not_seal_is_refused(client):
    _present(client, "not-a-renewal")

    refused = await client.post(REFRESH)

    assert (refused.status_code, refused.json()) == (401, {"detail": "SIGN_IN_ENDED"})


async def test_a_return_without_its_pending_sign_in_is_turned_away(client):
    back = await client.get(RETURN, params={"code": "icd_x", "state": "anything"})

    assert back.headers["location"] == "/?sign_in_error=SIGN_IN_EXPIRED"
    assert "core_session" not in client.cookies


async def test_a_return_for_another_sign_in_is_turned_away(
    client, initiative_stand_in, community_ref
):
    started = await client.get(START)
    back = initiative_stand_in.authorize(started.headers["location"], community_ref=community_ref)

    forged = await client.get(back.removeprefix(ORIGIN).replace("state=", "state=forged", 1))

    assert forged.headers["location"] == "/?sign_in_error=SIGN_IN_FAILED"
    assert "core_session" not in client.cookies


async def test_a_sign_in_cancelled_at_initiative_says_so(client):
    started = await client.get(START)
    state = URL(started.headers["location"]).params["state"]

    back = await client.get(RETURN, params={"error": "access_denied", "state": state})

    assert back.headers["location"] == "/?sign_in_error=SIGN_IN_CANCELLED"


@pytest.mark.parametrize(
    "elsewhere", ["https://elsewhere.example/", "//elsewhere.example", "/\\elsewhere", "/api/v1/x"]
)
async def test_a_sign_in_lands_only_on_the_apps_own_pages(
    client, initiative_stand_in, community_ref, elsewhere
):
    landed = await sign_in(client, initiative_stand_in, community_ref, next=elsewhere)

    assert landed.headers["location"] == "/"


async def test_a_community_level_place_opens_for_its_admins_alone(
    client, initiative_stand_in, community_ref
):
    landed = await sign_in(
        client, initiative_stand_in, community_ref, page="settings", initiative_id=None
    )

    assert landed.headers["location"] == "/?sign_in_error=COMMUNITY_PAGE_ADMIN_ONLY"
    assert "core_session" not in client.cookies


def test_a_sealed_value_reads_back_only_for_its_own_use_and_unaltered():
    sealer = Sealer("s" * 32)
    sealed = sealer.seal("renewal", {"o": 1})

    assert sealer.unseal("renewal", sealed)["o"] == 1
    with pytest.raises(Unsealable):
        sealer.unseal("pending", sealed)
    with pytest.raises(Unsealable):
        sealer.unseal("renewal", sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1])
    with pytest.raises(Unsealable):
        Sealer("t" * 32).unseal("renewal", sealed)
    with pytest.raises(Unsealable):
        sealer.unseal("renewal", sealed, max_age=timedelta(seconds=-1))


def test_a_sealing_key_too_short_is_refused():
    with pytest.raises(ValueError):
        Sealer("short")
