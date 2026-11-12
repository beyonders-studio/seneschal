"""Suppliers among the community's contacts, through Initiative's plug-in API."""

from __future__ import annotations

import json

import httpx2

from seneschal_core.testing import InitiativeStandIn


def _contact(id_: int, name: str) -> dict:
    return {"id": id_, "name": name, "is_organization": True, "emails": []}


async def test_the_community_contacts_are_read_page_by_page(
    client, sign_in, initiative_stand_in: InitiativeStandIn
):
    pages = {"1": [_contact(4, "Mill & Co")], "2": [_contact(9, "Dairy")]}
    scopes: list[str | None] = []

    def contacts(request: httpx2.Request) -> httpx2.Response:
        scopes.append(request.url.params.get("scope"))
        page = request.url.params["page"]
        return httpx2.Response(200, json={"items": pages[page], "has_next": page == "1"})

    initiative_stand_in.routes[("GET", "/contacts/")] = contacts
    await sign_in(client)

    answer = await client.get("/api/v1/contacts")

    assert answer.json() == [
        {"id": 4, "name": "Mill & Co", "is_organization": True},
        {"id": 9, "name": "Dairy", "is_organization": True},
    ]
    assert scopes == ["community", "community"]


async def test_a_supplier_is_added_to_the_community_contacts(
    client, sign_in, initiative_stand_in: InitiativeStandIn
):
    sent: list[dict] = []

    def add(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(201, json=_contact(12, "Beans Ltd"))

    initiative_stand_in.routes[("POST", "/contacts/")] = add
    # Opened from an initiative or not, the contact is the community's.
    for initiative_id in (3, None):
        await sign_in(client, admin=True, initiative_id=initiative_id)
        answer = await client.post("/api/v1/contacts", json={"name": " Beans Ltd "})
        assert (answer.status_code, answer.json()) == (
            201,
            {"id": 12, "name": "Beans Ltd", "is_organization": True},
        )
    assert sent == [{"name": "Beans Ltd", "is_organization": True}] * 2


async def test_a_refused_supplier_says_why(client, sign_in, initiative_stand_in: InitiativeStandIn):
    await sign_in(client)
    for detail, code in (
        ("CONTACT_COMMUNITY_PLUGIN_REQUIRED", "COMMUNITY_CONTACTS_NOT_INSTALLED"),
        ("CONTACT_PLUGIN_INITIATIVE_REQUIRED", "CONTACT_ADD_REFUSED"),
        ("CONTACTS_DISABLED", "CONTACT_ADD_REFUSED"),
    ):
        initiative_stand_in.routes[("POST", "/contacts/")] = lambda _r, d=detail: httpx2.Response(
            403, json={"detail": d}
        )
        refused = await client.post("/api/v1/contacts", json={"name": "Beans Ltd"})
        assert (refused.status_code, refused.json()) == (403, {"detail": code})


async def test_contacts_wait_for_initiative(
    client, sign_in, initiative_stand_in: InitiativeStandIn
):
    initiative_stand_in.routes[("GET", "/contacts/")] = lambda _r: httpx2.Response(502)
    initiative_stand_in.routes[("POST", "/contacts/")] = lambda _r: httpx2.Response(503)
    await sign_in(client)

    read = await client.get("/api/v1/contacts")
    added = await client.post("/api/v1/contacts", json={"name": "Beans Ltd"})

    for answer in (read, added):
        assert (answer.status_code, answer.json()) == (503, {"detail": "INITIATIVE_UNAVAILABLE"})
