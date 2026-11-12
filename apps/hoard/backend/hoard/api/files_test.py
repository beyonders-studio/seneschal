"""Initiative's files, searched acting for the member viewing the page."""

from __future__ import annotations

import httpx2

from seneschal_core.testing import InitiativeStandIn


def _file(id_: int, name: str, initiative: str | None = "Kitchen") -> dict:
    return {
        "id": id_,
        "name": name,
        "file_type": "upload",
        "initiative": {"id": 1, "name": initiative} if initiative else None,
    }


async def test_a_search_finds_the_files_the_member_may_open(
    client, sign_in, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    asked: list[tuple[str | None, dict]] = []

    def files(request: httpx2.Request) -> httpx2.Response:
        asked.append((initiative_stand_in.member_of(request), dict(request.url.params)))
        return httpx2.Response(
            200, json={"items": [_file(4, "Flour certificate"), {"id": "x"}], "has_next": False}
        )

    initiative_stand_in.routes[("GET", "/files/")] = files
    initiative_stand_in.consents.add((community_ref, "member-ref"))
    await sign_in(client)

    found = await client.get("/api/v1/files", params={"search": " flour "})
    recent = await client.get("/api/v1/files")

    assert found.json() == [
        {"id": 4, "name": "Flour certificate", "file_type": "upload", "initiative_name": "Kitchen"}
    ]
    assert asked == [
        ("member-ref", {"page_size": "20", "search": "flour"}),
        ("member-ref", {"page_size": "20"}),
    ]
    assert recent.status_code == 200


async def test_the_member_is_asked_to_consent_first(
    client, sign_in, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("GET", "/files/")] = lambda _r: httpx2.Response(
        200, json={"items": [], "has_next": False}
    )
    await sign_in(client)

    refused = await client.get("/api/v1/files")
    asked = await client.post("/api/v1/files/consent")
    initiative_stand_in.consents.add((community_ref, "member-ref"))
    granted = await client.post("/api/v1/files/consent")
    found = await client.get("/api/v1/files")

    assert (refused.status_code, refused.json()) == (403, {"detail": "FILES_CONSENT_REQUIRED"})
    assert [asked.json(), granted.json()] == [{"status": "pending"}, {"status": "granted"}]
    assert [body["member"] for _, body in initiative_stand_in.consent_requests] == [
        "member-ref",
        "member-ref",
    ]
    assert (found.status_code, found.json()) == (200, [])


async def test_a_search_waits_for_initiative(
    client, sign_in, initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("GET", "/files/")] = lambda _r: httpx2.Response(502)
    initiative_stand_in.consents.add((community_ref, "member-ref"))
    await sign_in(client)

    refused = await client.get("/api/v1/files")

    assert (refused.status_code, refused.json()) == (503, {"detail": "INITIATIVE_UNAVAILABLE"})
