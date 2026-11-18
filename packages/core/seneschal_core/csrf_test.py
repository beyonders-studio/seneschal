"""Only the app's own pages change things with its cookies."""

from __future__ import annotations

REFRESH = "/api/v1/auth/refresh"
EXCHANGE = "/api/v1/auth/handoff/exchange"


async def test_a_change_with_the_apps_cookie_from_another_site_is_refused(client):
    client.cookies.set("core_session", "anything")

    refused = await client.post(REFRESH, headers={"Origin": "https://elsewhere.example"})

    assert (refused.status_code, refused.json()) == (403, {"detail": "CROSS_SITE_REQUEST"})


async def test_a_change_with_the_apps_cookie_and_no_origin_is_refused(client):
    client.cookies.set("core_renew", "anything")
    del client.headers["origin"]

    assert (await client.post(REFRESH)).status_code == 403


async def test_the_browser_saying_same_origin_is_taken(client):
    client.cookies.set("core_renew", "anything")
    del client.headers["origin"]

    answered = await client.post(REFRESH, headers={"Sec-Fetch-Site": "same-origin"})

    assert answered.json() == {"detail": "SIGN_IN_ENDED"}


async def test_a_request_without_the_apps_cookies_is_left_alone(client):
    client.cookies.clear()

    answered = await client.post(
        EXCHANGE,
        json={"handoff_token": "x"},
        headers={"Origin": "https://elsewhere.example"},
    )

    assert answered.json() == {"detail": "INVALID_HANDOFF_TOKEN"}


async def test_reading_is_never_refused(client):
    client.cookies.set("core_session", "anything")

    answered = await client.get("/api/v1/auth/me", headers={"Origin": "https://elsewhere.example"})

    assert answered.json() == {"detail": "NOT_AUTHENTICATED"}
