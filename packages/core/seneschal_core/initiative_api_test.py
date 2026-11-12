"""Calling Initiative's plug-in API as a community's install, against a stand-in."""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx2
import jwt
import pytest

from seneschal_core.initiative_api import (
    ConsentRequired,
    InitiativeApi,
    InitiativeRefused,
    InitiativeUnavailable,
    key_id,
)
from seneschal_core.testing import InitiativeStandIn, new_key_pem, private_key_pem


@pytest.fixture
async def api(initiative_stand_in: InitiativeStandIn):
    api = initiative_stand_in.api()
    yield api
    await api.aclose()


def _issued_for(stand_in: InitiativeStandIn, community_ref: str) -> int:
    return sum(1 for community in stand_in.tokens.values() if community == community_ref)


async def test_it_names_members_as_the_communitys_install(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.people.update(
        {
            "ref-ada": {"display_name": "Ada", "username": "ada"},
            "ref-bo": {"username": "bo", "discriminator": 4},
        }
    )

    people = await api.people(community_ref, ["ref-ada", "ref-bo", "ref-gone", "ref-ada"])

    assert {ref: person.name for ref, person in people.items()} == {
        "ref-ada": "Ada",
        "ref-bo": "bo",
    }
    assert set(initiative_stand_in.tokens.values()) == {community_ref}


async def test_a_long_list_of_references_is_looked_up_in_batches(
    initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.people.update({f"ref-{n}": {"username": f"m{n}"} for n in range(150)})
    batches: list[int] = []
    search = initiative_stand_in.routes[("GET", "/users/search")]

    def counted(request: httpx2.Request) -> httpx2.Response:
        batches.append(len(request.url.params.get_list("user_id")))
        return search(request)

    initiative_stand_in.routes[("GET", "/users/search")] = counted
    api = initiative_stand_in.api()
    try:
        people = await api.people(community_ref, [f"ref-{n}" for n in range(150)])
    finally:
        await api.aclose()

    assert (len(people), batches) == (150, [100, 50])


async def test_its_assertion_is_signed_for_the_token_endpoint(initiative_stand_in, community_ref):
    sent: list[httpx2.Request] = []
    stand_in_http = initiative_stand_in.http()

    async def answer(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return await stand_in_http.send(request)

    api = InitiativeApi(
        api_url=f"{InitiativeStandIn.API_URL}/",
        public_id=initiative_stand_in.public_id,
        key_pem=private_key_pem(initiative_stand_in.plugin_key),
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(answer)),
    )
    try:
        await api.people(community_ref, ["ref-anyone"])
    finally:
        await api.aclose()
        await stand_in_http.aclose()

    form = dict(parse_qsl(sent[0].content.decode()))
    assert (form["grant_type"], form["installation"]) == ("client_credentials", community_ref)
    header = jwt.get_unverified_header(form["client_assertion"])
    assert (header["alg"], header["kid"]) == ("EdDSA", key_id(initiative_stand_in.plugin_key))
    claims = jwt.decode(form["client_assertion"], options={"verify_signature": False})
    assert claims["aud"] == f"{InitiativeStandIn.API_URL}/plugin-platform/oauth/token"
    assert claims["iss"] == claims["sub"] == initiative_stand_in.public_id
    assert claims["exp"] - claims["iat"] == 60
    assert sent[1].headers["authorization"] == f"Bearer {next(iter(initiative_stand_in.tokens))}"


async def test_a_token_is_kept_until_initiative_stops_taking_it(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    await api.people(community_ref, ["ref-a"])
    await api.people(community_ref, ["ref-b"])
    assert _issued_for(initiative_stand_in, community_ref) == 1

    initiative_stand_in.tokens.clear()
    await api.people(community_ref, ["ref-c"])

    assert _issued_for(initiative_stand_in, community_ref) == 1
    await api.people(f"{community_ref}-other", ["ref-d"])
    assert len(initiative_stand_in.tokens) == 2


async def test_a_token_narrowed_to_an_initiative_is_kept_apart(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("GET", "/tasks/")] = lambda _request: httpx2.Response(200, json=[])

    await api.call(community_ref, "GET", "/tasks/")
    await api.call(community_ref, "GET", "/tasks/", narrowed_to=12)
    await api.call(community_ref, "GET", "/tasks/", narrowed_to=12)

    assert sorted(initiative_stand_in.narrowed.values(), key=str) == [12, None]


async def test_a_call_acts_for_a_member_once_they_consent(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("GET", "/files/")] = lambda request: httpx2.Response(
        200, json={"member": initiative_stand_in.member_of(request)}
    )

    with pytest.raises(ConsentRequired):
        await api.call(community_ref, "GET", "/files/", acting_for="ref-ada")
    asked = await api.ask_consent(community_ref, "ref-ada", "Find files to link")
    initiative_stand_in.consents.add((community_ref, "ref-ada"))
    acting = await api.call(community_ref, "GET", "/files/", acting_for="ref-ada")
    own = await api.call(community_ref, "GET", "/files/")

    assert asked["status"] == "pending"
    assert initiative_stand_in.consent_requests == [
        (community_ref, {"member": "ref-ada", "label": "Find files to link", "access": "read"})
    ]
    assert (acting, own) == ({"member": "ref-ada"}, {"member": None})


async def test_it_emits_events_and_keeps_values_on_items_as_the_install(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    await api.emit_event(community_ref, "plugin.x.run-started", {"run_id": 4}, initiative_id=12)
    kept = await api.write_metadata(
        community_ref, "task", 41, values={"x.status": "in_progress"}, narrowed_to=12
    )
    cleared = await api.write_metadata(community_ref, "task", 41, values={"x.status": None})

    assert initiative_stand_in.events == [
        (
            community_ref,
            {"event_type": "plugin.x.run-started", "payload": {"run_id": 4}, "initiative_id": 12},
        )
    ]
    assert (kept, cleared) == ({"x.status": "in_progress"}, {})


async def test_an_error_answer_is_a_refusal_with_its_detail(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("POST", "/contacts/")] = lambda _request: httpx2.Response(
        403, json={"detail": "PLUGIN_SCOPE_REQUIRED"}
    )

    with pytest.raises(InitiativeRefused) as refused:
        await api.call(community_ref, "POST", "/contacts/", body={"name": "Mill"})

    assert (refused.value.status_code, refused.value.detail) == (403, "PLUGIN_SCOPE_REQUIRED")


async def test_without_a_registered_key_initiative_cannot_be_called(
    initiative_stand_in: InitiativeStandIn, community_ref: str
):
    for key_pem in (None, new_key_pem()):
        api = InitiativeApi(
            api_url=InitiativeStandIn.API_URL,
            public_id=initiative_stand_in.public_id,
            key_pem=key_pem,
            http=initiative_stand_in.http(),
        )
        try:
            with pytest.raises(InitiativeUnavailable):
                await api.people(community_ref, ["ref-a"])
        finally:
            await api.aclose()
    assert initiative_stand_in.tokens == {}


async def test_no_answer_or_a_failing_one_is_unavailable(initiative_stand_in, community_ref):
    def down(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    api = InitiativeApi(
        api_url=InitiativeStandIn.API_URL,
        public_id=initiative_stand_in.public_id,
        key_pem=private_key_pem(initiative_stand_in.plugin_key),
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(down)),
    )
    try:
        with pytest.raises(InitiativeUnavailable):
            await api.people(community_ref, ["ref-a"])
    finally:
        await api.aclose()

    initiative_stand_in.routes[("GET", "/users/search")] = lambda _r: httpx2.Response(502)
    failing = initiative_stand_in.api()
    try:
        with pytest.raises(InitiativeUnavailable):
            await failing.people(community_ref, ["ref-a"])
    finally:
        await failing.aclose()


async def test_it_reads_the_installs_configuration_in_its_community(
    api: InitiativeApi, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    shopify = {"shop": "tea.myshopify.com", "client_secret": "s3cret"}
    initiative_stand_in.connections[community_ref] = {"shopify": shopify}

    config = await api.installation_config(community_ref)
    elsewhere = await api.installation_config(f"{community_ref}-other")

    assert (config["community_ref"], config["connections"]) == (community_ref, {"shopify": shopify})
    assert elsewhere["connections"] == {}
