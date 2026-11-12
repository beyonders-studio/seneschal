"""Files linked to records, named as the member viewing them may see them,
and the backup period the organization's rules ask for."""

from __future__ import annotations

import httpx2

from hoard import documents
from hoard.records import OrganizationSettings
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn


def _files(initiative_stand_in: InitiativeStandIn, names: dict[int, str]) -> list[str | None]:
    """Answer each file ``names`` names, to its reader; returns who read them."""
    readers: list[str | None] = []

    def read(request: httpx2.Request) -> httpx2.Response:
        readers.append(initiative_stand_in.member_of(request))
        assert request.url.params["include_content"] == "false"
        file_id = int(request.url.path.rsplit("/", 1)[1])
        if file_id not in names:
            return httpx2.Response(404, json={"detail": "FILE_NOT_FOUND"})
        return httpx2.Response(
            200, json={"id": file_id, "name": names[file_id], "file_type": "lexical"}
        )

    for file_id in (*names, 66):
        initiative_stand_in.routes[("GET", f"/files/{file_id}")] = read
    return readers


async def test_a_lot_lists_its_files_by_the_names_the_member_may_see(
    client, sign_in, db: WriteSession, material, lot, initiative_stand_in, community_ref
):
    flour = await material("Flour")
    sack = await lot(flour.id, "F-1", "3")
    await documents.link(db, 31, lot_id=sack.id)
    await documents.link(db, 66, lot_id=sack.id)
    await documents.link(db, 12, material_id=flour.id)
    await db.commit()
    readers = _files(initiative_stand_in, {31: "Complaint, 3 Nov", 12: "Certificate"})
    initiative_stand_in.consents.add((community_ref, "member-ref"))
    await sign_in(client)

    listed = (await client.get("/api/v1/documents", params={"lot_id": sack.id})).json()
    neither = await client.get("/api/v1/documents")
    both = await client.get("/api/v1/documents", params={"lot_id": sack.id, "base_id": 1})

    assert [(each["file_id"], each["name"]) for each in listed] == [
        (31, "Complaint, 3 Nov"),
        (66, None),
    ]
    assert listed[0]["created_by"] == "member-ref"
    assert set(readers) == {"member-ref"}
    for refused in (neither, both):
        assert (refused.status_code, refused.json()) == (422, {"detail": "ONE_RECORD_REQUIRED"})


async def test_a_member_links_a_file_they_may_open_and_unlinks_it(
    client, sign_in, db: WriteSession, base, initiative_stand_in, community_ref
):
    batter = await base("Batter")
    await db.commit()
    _files(initiative_stand_in, {40: "Traceability plan"})
    initiative_stand_in.consents.add((community_ref, "member-ref"))
    await sign_in(client)

    linked = await client.post("/api/v1/documents", json={"file_id": 40, "base_id": batter.id})
    again = await client.post("/api/v1/documents", json={"file_id": 40, "base_id": batter.id})
    hidden = await client.post("/api/v1/documents", json={"file_id": 66, "base_id": batter.id})
    nowhere = await client.post("/api/v1/documents", json={"file_id": 40, "lot_id": 999999})
    unnamed = await client.post("/api/v1/documents", json={"file_id": 40})

    assert linked.status_code == 201
    assert (linked.json()["name"], again.json()["id"]) == ("Traceability plan", linked.json()["id"])
    assert (hidden.status_code, hidden.json()) == (404, {"detail": "FILE_NOT_FOUND"})
    assert (nowhere.status_code, nowhere.json()) == (404, {"detail": "NOT_FOUND"})
    assert (unnamed.status_code, unnamed.json()) == (422, {"detail": "ONE_RECORD_REQUIRED"})

    unlinked = await client.delete(f"/api/v1/documents/{linked.json()['id']}")
    gone = await client.delete(f"/api/v1/documents/{linked.json()['id']}")

    assert (unlinked.status_code, gone.status_code) == (204, 404)
    assert (await client.get("/api/v1/documents", params={"base_id": batter.id})).json() == []


async def test_documents_wait_for_the_member_to_consent(
    client, sign_in, db: WriteSession, material, lot, initiative_stand_in
):
    flour = await material("Flour")
    sack = await lot(flour.id, "F-1", "3")
    await documents.link(db, 31, lot_id=sack.id)
    await db.commit()
    _files(initiative_stand_in, {31: "Complaint"})
    await sign_in(client)

    listed = await client.get("/api/v1/documents", params={"lot_id": sack.id})
    linked = await client.post("/api/v1/documents", json={"file_id": 31, "lot_id": sack.id})

    for refused in (listed, linked):
        assert (refused.status_code, refused.json()) == (403, {"detail": "FILES_CONSENT_REQUIRED"})


async def test_the_backup_period_is_the_longest_the_ticked_rules_ask(
    client, sign_in, db: WriteSession
):
    await sign_in(client)
    none = (await client.get("/api/v1/documents/backup-period")).json()
    db.add(
        OrganizationSettings(
            organization_id=db.organization_id, regimes=["us_one_up_one_back", "eu_food"]
        )
    )
    await db.commit()

    ticked = (await client.get("/api/v1/documents/backup-period")).json()

    assert (none["months"], ticked["months"]) == (None, 60)
    assert none["regimes"][0] == {"regime": "us_one_up_one_back", "months": 24}
    assert len(none["regimes"]) == 7
