"""Suppliers among the community's contacts, through Initiative's plug-in API."""

from __future__ import annotations

import json

import httpx2

from seneschal_core.testing import InitiativeStandIn


def _contact(id_: int, name: str) -> dict:
    return {"id": id_, "name": name, "is_organization": True, "emails": []}


async def test_contacts_are_read_page_by_page(
    client, sign_in, initiative_stand_in: InitiativeStandIn
):
    pages = {"1": [_contact(4, "Mill & Co")], "2": [_contact(9, "Dairy")]}

    def contacts(request: httpx2.Request) -> httpx2.Response:
        page = request.url.params["page"]
        return httpx2.Response(200, json={"items": pages[page], "has_next": page == "1"})

    initiative_stand_in.routes[("GET", "/contacts/")] = contacts
    await sign_in(client)

    answer = await client.get("/api/v1/contacts")

    assert answer.json() == [
        {"id": 4, "name": "Mill & Co", "is_organization": True},
        {"id": 9, "name": "Dairy", "is_organization": True},
    ]


async def test_a_supplier_is_added_in_the_page_initiative(
    client, sign_in, initiative_stand_in: InitiativeStandIn
):
    sent: list[dict] = []

    def add(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(201, json=_contact(12, "Beans Ltd"))

    initiative_stand_in.routes[("POST", "/contacts/")] = add
    await sign_in(client, initiative_id=3)

    answer = await client.post("/api/v1/contacts", json={"name": " Beans Ltd "})

    assert (answer.status_code, answer.json()) == (
        201,
        {"id": 12, "name": "Beans Ltd", "is_organization": True},
    )
    assert sent == [{"name": "Beans Ltd", "is_organization": True, "initiative_id": 3}]

    initiative_stand_in.routes[("POST", "/contacts/")] = lambda _r: httpx2.Response(
        403, json={"detail": "CONTACTS_DISABLED"}
    )
    refused = await client.post("/api/v1/contacts", json={"name": "Beans Ltd"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "CONTACT_REFUSED"})

    await sign_in(client, admin=True, initiative_id=None)
    outside = await client.post("/api/v1/contacts", json={"name": "Beans Ltd"})
    assert (outside.status_code, outside.json()) == (403, {"detail": "CONTACT_REFUSED"})


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
