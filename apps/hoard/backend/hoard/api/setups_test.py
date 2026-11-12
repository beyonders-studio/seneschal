"""Starting setups: offered to everyone, added by a community admin, who may
start blank instead, once."""

from __future__ import annotations


async def test_an_admin_adds_a_setup_in_their_own_words(client, sign_in):
    await sign_in(client, admin=True)
    offered = (await client.get("/api/v1/setups")).json()
    [drinks] = offered["setups"]
    words = {word: word.rsplit(".", 1)[-1] for word in drinks["words"]}

    short = await client.post("/api/v1/setups/drinks/add", json={"words": {}})
    added = await client.post("/api/v1/setups/drinks/add", json={"words": words})
    unknown = await client.post("/api/v1/setups/bakery/add", json={"words": words})
    again = await client.post("/api/v1/setups/drinks/add", json={"words": words})

    assert (offered["started"], drinks["key"], drinks["added"]) == (False, "drinks", False)
    assert (short.status_code, short.json()) == (422, {"detail": "WORDS_MISSING"})
    assert (added.status_code, added.json()["added"]) == (200, True)
    assert unknown.status_code == 404
    # A setup is chosen once, never added later.
    assert (again.status_code, again.json()) == (409, {"detail": "SETUP_STARTED"})
    assert (await client.get("/api/v1/setups")).json()["started"] is True
    units = (await client.get("/api/v1/settings/units")).json()
    assert {"g", "fl_oz", "bottle_750ml"} <= {unit["name"] for unit in units}


async def test_a_member_neither_adds_a_setup_nor_starts_blank(client, sign_in):
    await sign_in(client)

    answer = await client.post("/api/v1/setups/drinks/add", json={"words": {}})
    blank = await client.post("/api/v1/setups/blank")

    assert (answer.status_code, answer.json()) == (403, {"detail": "ADMIN_ONLY"})
    assert (blank.status_code, blank.json()) == (403, {"detail": "ADMIN_ONLY"})


async def test_an_admin_starts_blank(client, sign_in):
    await sign_in(client, admin=True)

    blank = await client.post("/api/v1/setups/blank")
    later = await client.post("/api/v1/setups/drinks/add", json={"words": {}})

    assert (blank.status_code, blank.json()["started"]) == (200, True)
    assert (await client.get("/api/v1/setups")).json()["started"] is True
    assert (later.status_code, later.json()) == (409, {"detail": "SETUP_STARTED"})
    assert (await client.get("/api/v1/settings/units")).json() == []
