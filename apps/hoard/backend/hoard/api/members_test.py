"""Members' names, from Initiative, for the references records keep."""

from __future__ import annotations

import httpx2

from seneschal_core.testing import InitiativeStandIn


async def test_a_page_reads_names_and_a_reference_gone_has_none(
    client, sign_in, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.people.update(
        {"ref-ada": {"display_name": "Ada", "username": "ada"}, "ref-bo": {"username": "bo"}}
    )
    await sign_in(client)

    answer = await client.get(
        "/api/v1/members", params={"member_ref": ["ref-bo", "ref-gone", "ref-ada", "ref-bo"]}
    )

    assert answer.json() == [
        {"member_ref": "ref-bo", "name": "bo"},
        {"member_ref": "ref-gone", "name": None},
        {"member_ref": "ref-ada", "name": "Ada"},
    ]
    assert set(initiative_stand_in.tokens.values()) == {community_ref}


async def test_names_wait_for_initiative(client, sign_in, initiative_stand_in: InitiativeStandIn):
    initiative_stand_in.routes[("GET", "/users/search")] = lambda _r: httpx2.Response(502)
    await sign_in(client)

    answer = await client.get("/api/v1/members", params={"member_ref": "ref-ada"})

    assert (answer.status_code, answer.json()) == (503, {"detail": "INITIATIVE_UNAVAILABLE"})


async def test_names_are_only_for_a_signed_in_page(client):
    answer = await client.get("/api/v1/members", params={"member_ref": "ref-ada"})

    assert (answer.status_code, answer.json()) == (401, {"detail": "NOT_AUTHENTICATED"})
