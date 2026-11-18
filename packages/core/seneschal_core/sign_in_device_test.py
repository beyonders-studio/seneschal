"""A phone app's sign-in: through the system browser, with a code spent once,
a Bearer session, and a renewal bound to its install."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field

from httpx2 import URL, AsyncClient

from seneschal_core.testing import InitiativeStandIn

START = "/api/v1/auth/sign-in/start"
TOKEN = "/api/v1/auth/device/token"
DEVICE_REFRESH = "/api/v1/auth/device/refresh"
DEVICE_SIGN_OUT = "/api/v1/auth/device/sign-out"
BROWSER_REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"
ORIGIN = "http://test"
CALLBACK = "coretest://sign-in/callback?"


@dataclass
class PhoneApp:
    """What the phone app makes and keeps for one sign-in."""

    install: str = field(default_factory=lambda: str(uuid.uuid4()))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    state: str = field(default_factory=lambda: secrets.token_urlsafe(24))

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def browse(
    client: AsyncClient,
    initiative: InitiativeStandIn,
    community_ref: str,
    app: PhoneApp,
    **place: object,
) -> URL:
    """The system browser: start, sign in at Initiative, come back; answers
    the address it hands the app."""
    started = await client.get(
        START,
        params={
            "client": "device",
            "challenge": app.challenge,
            "state": app.state,
            "install": app.install,
        },
    )
    assert started.status_code == 303, started.text
    back = initiative.authorize(
        started.headers["location"],
        community_ref=community_ref,
        **place,  # type: ignore[arg-type]
    )
    returned = await client.get(back.removeprefix(ORIGIN))
    location = returned.headers["location"]
    assert location.startswith(CALLBACK), location
    url = URL(location)
    assert url.params["state"] == app.state
    return url


async def sign_in(
    client: AsyncClient, initiative: InitiativeStandIn, community_ref: str, app: PhoneApp
) -> dict:
    url = await browse(client, initiative, community_ref, app)
    answered = await client.post(
        TOKEN, json={"code": url.params["code"], "verifier": app.verifier, "install": app.install}
    )
    assert answered.status_code == 200, answered.text
    return answered.json()


def bearer(session: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {session}"}


async def test_a_phone_app_signs_in_through_the_browser_and_calls_with_its_session(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    signed_in = await sign_in(client, initiative_stand_in, community_ref, app)

    assert signed_in["me"]["community_ref"] == community_ref
    assert signed_in["renewal"]
    assert "core_session" not in client.cookies
    me = await client.get(ME, headers=bearer(signed_in["session"]))
    assert (me.status_code, me.json()["member_ref"]) == (200, "member-ref")


async def test_each_session_travels_only_the_way_it_was_made_to(
    client, initiative_stand_in, community_ref
):
    device = (await sign_in(client, initiative_stand_in, community_ref, PhoneApp()))["session"]

    client.cookies.set("core_session", device)
    assert (await client.get(ME)).status_code == 401

    client.cookies.clear()
    handoff = initiative_stand_in.handoff(community_ref=community_ref)
    await client.post("/api/v1/auth/handoff/exchange", json={"handoff_token": handoff})
    framed = client.cookies["core_session"]
    client.cookies.clear()
    assert (await client.get(ME, headers=bearer(framed))).status_code == 401


async def test_a_code_is_spent_at_the_first_attempt_and_a_wrong_one_ends_the_sign_in(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    code = (await browse(client, initiative_stand_in, community_ref, app)).params["code"]

    wrong = await client.post(
        TOKEN, json={"code": code, "verifier": secrets.token_urlsafe(48), "install": app.install}
    )
    again = await client.post(
        TOKEN, json={"code": code, "verifier": app.verifier, "install": app.install}
    )

    assert (wrong.status_code, wrong.json()) == (401, {"detail": "SIGN_IN_FAILED"})
    assert again.status_code == 401


async def test_a_renewal_spends_once_from_its_own_install(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    first = (await sign_in(client, initiative_stand_in, community_ref, app))["renewal"]

    renewed = await client.post(DEVICE_REFRESH, json={"renewal": first, "install": app.install})

    assert renewed.status_code == 200
    body = renewed.json()
    assert body["renewal"] != first
    assert (await client.get(ME, headers=bearer(body["session"]))).status_code == 200
    replayed = await client.post(DEVICE_REFRESH, json={"renewal": first, "install": app.install})
    assert replayed.status_code == 401
    # Presented again, the old renewal ended the sign-in it belonged to.
    current = await client.post(
        DEVICE_REFRESH, json={"renewal": body["renewal"], "install": app.install}
    )
    assert current.status_code == 401


async def test_a_renewal_from_another_install_ends_the_sign_in(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    renewal = (await sign_in(client, initiative_stand_in, community_ref, app))["renewal"]

    stolen = await client.post(
        DEVICE_REFRESH, json={"renewal": renewal, "install": str(uuid.uuid4())}
    )
    own = await client.post(DEVICE_REFRESH, json={"renewal": renewal, "install": app.install})

    assert (stolen.status_code, own.status_code) == (401, 401)


async def test_a_phones_renewal_is_not_taken_at_the_browsers_route(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    renewal = (await sign_in(client, initiative_stand_in, community_ref, app))["renewal"]

    client.cookies.set("core_renew", renewal)
    assert (await client.post(BROWSER_REFRESH)).status_code == 401
    client.cookies.clear()
    own = await client.post(DEVICE_REFRESH, json={"renewal": renewal, "install": app.install})
    assert own.status_code == 401


async def test_signing_in_again_from_an_install_ends_its_earlier_sign_in(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    earlier = (await sign_in(client, initiative_stand_in, community_ref, app))["renewal"]
    again = PhoneApp(install=app.install)
    later = (await sign_in(client, initiative_stand_in, community_ref, again))["renewal"]

    ended = await client.post(DEVICE_REFRESH, json={"renewal": earlier, "install": app.install})
    stands = await client.post(DEVICE_REFRESH, json={"renewal": later, "install": app.install})

    assert (ended.status_code, stands.status_code) == (401, 200)


async def test_signing_out_on_the_phone_ends_its_sign_in(
    client, initiative_stand_in, community_ref
):
    app = PhoneApp()
    renewal = (await sign_in(client, initiative_stand_in, community_ref, app))["renewal"]

    signed_out = await client.post(
        DEVICE_SIGN_OUT, json={"renewal": renewal, "install": app.install}
    )

    assert signed_out.status_code == 204
    refused = await client.post(DEVICE_REFRESH, json={"renewal": renewal, "install": app.install})
    assert refused.status_code == 401


async def test_a_phone_sign_in_needs_its_challenge_state_and_install(client):
    app = PhoneApp()
    for missing in ("challenge", "state", "install"):
        params = {
            "client": "device",
            "challenge": app.challenge,
            "state": app.state,
            "install": app.install,
        }
        del params[missing]

        refused = await client.get(START, params=params)

        assert refused.status_code == 400, missing


async def test_a_phone_sign_in_cancelled_at_initiative_goes_back_to_the_app_saying_so(
    client, initiative_stand_in
):
    app = PhoneApp()
    started = await client.get(
        START,
        params={
            "client": "device",
            "challenge": app.challenge,
            "state": app.state,
            "install": app.install,
        },
    )
    oidc_state = URL(started.headers["location"]).params["state"]

    back = await client.get(
        "/api/v1/auth/sign-in/return", params={"error": "access_denied", "state": oidc_state}
    )

    url = URL(back.headers["location"])
    assert str(url).startswith(CALLBACK)
    assert (url.params["error"], url.params["state"]) == ("SIGN_IN_CANCELLED", app.state)


async def test_a_phone_app_reads_which_app_a_server_is_and_that_phones_may_sign_in(client):
    offer = await client.get("/api/v1/auth/sign-in")

    assert offer.json() == {"app": "core", "browser": True, "device": True}
