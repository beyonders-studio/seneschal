"""Tokens Initiative signs and statements billing signs: what verifies, and
what is refused."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from seneschal_core.initiative import (
    CONTEXT_TYPE,
    HANDOFF_TYPE,
    ContextSubject,
    Initiative,
    StatementRefused,
    TokenRefused,
)
from seneschal_core.organizations import CommunityStatus, Statement
from seneschal_core.testing import InitiativeStandIn


@pytest.fixture
async def initiative(initiative_stand_in: InitiativeStandIn) -> AsyncIterator[Initiative]:
    initiative = initiative_stand_in.initiative()
    yield initiative
    await initiative.aclose()


async def test_a_handoff_verifies_to_what_it_says(initiative, initiative_stand_in):
    token = initiative_stand_in.handoff(
        community_ref="community-1",
        member_ref="member-7",
        page="production",
        initiative_id=12,
        admin=True,
        permissions=("use", "costs"),
        community_status="read_only",
        plugin_install_id=40,
    )

    claims = await initiative.verify_handoff(token)

    assert (
        claims.member_ref,
        claims.community_ref,
        claims.page,
        claims.initiative_id,
        claims.admin,
        claims.permissions,
        claims.community_status,
        claims.plugin_install_id,
    ) == (
        "member-7",
        "community-1",
        "production",
        12,
        True,
        ("use", "costs"),
        CommunityStatus.READ_ONLY,
        40,
    )


async def test_a_handoff_without_an_initiative_or_permissions_reads_as_a_community_page(
    initiative, initiative_stand_in
):
    token = initiative_stand_in.sign(
        {"sub": "m", "community_ref": "c", "surface_id": "settings"}, typ=HANDOFF_TYPE
    )

    claims = await initiative.verify_handoff(token)

    assert (claims.initiative_id, claims.admin, claims.permissions) == (None, False, ())


@pytest.mark.parametrize(
    ("claims", "signing", "reason"),
    [
        ({}, {"typ": CONTEXT_TYPE}, "not a page handoff"),
        ({"aud": "initiative-plugin:beyonders-studio.another"}, {}, "did not verify"),
        ({"iss": "elsewhere"}, {}, "did not verify"),
        ({}, {"lifetime": timedelta(seconds=-120)}, "did not verify"),
        ({}, {"key": ed25519.Ed25519PrivateKey.generate()}, "did not verify"),
        ({"community_status": "paused"}, {}, "community_status"),
        ({"community_ref": ""}, {}, "community_ref"),
        ({"permissions": "use"}, {}, "permissions"),
        ({"permissions": ["use", 3]}, {}, "permissions"),
        ({"initiative_id": "12"}, {}, "initiative_id"),
        ({"plugin_install_id": True}, {}, "plugin_install_id"),
    ],
)
async def test_a_handoff_that_is_not_one_for_this_app_is_refused(
    initiative, initiative_stand_in, claims, signing, reason
):
    token = initiative_stand_in.sign(
        {"sub": "m", "community_ref": "c", "surface_id": "stock", "initiative_id": 1, **claims},
        **{"typ": HANDOFF_TYPE, **signing},
    )

    with pytest.raises(TokenRefused, match=reason):
        await initiative.verify_handoff(token)


async def test_a_handoff_signed_any_other_way_is_refused(initiative, initiative_stand_in):
    claims = {"sub": "m", "community_ref": "c", "surface_id": "stock"}
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    for token in (
        initiative_stand_in.sign(claims, typ=HANDOFF_TYPE, key=rsa_key, algorithm="RS256"),
        initiative_stand_in.sign(claims, typ=HANDOFF_TYPE, key="secret" * 6, algorithm="HS256"),
    ):
        with pytest.raises(TokenRefused, match="EdDSA"):
            await initiative.verify_handoff(token)


async def test_a_context_token_names_its_endpoint(initiative, initiative_stand_in):
    token = initiative_stand_in.context("plugin.x.read", community_ref="c")

    claims = await initiative.verify_context(token)

    assert (claims.community_ref, claims.endpoint_id, claims.hook) == ("c", "plugin.x.read", None)


async def test_a_lifecycle_token_is_taken_only_for_its_hook(initiative, initiative_stand_in):
    token = initiative_stand_in.lifecycle("erase", community_ref="c")

    assert (await initiative.verify_lifecycle(token, hook="erase")).hook == "erase"
    with pytest.raises(TokenRefused, match="installed"):
        await initiative.verify_lifecycle(token, hook="installed")
    with pytest.raises(TokenRefused, match="scope endpoint"):
        await initiative.verify_context(token)


async def test_tokens_are_not_taken_for_each_other(initiative, initiative_stand_in):
    handoff = initiative_stand_in.handoff(community_ref="c")
    context = initiative_stand_in.context("plugin.x.read", community_ref="c")

    with pytest.raises(TokenRefused):
        await initiative.verify_context(handoff)
    with pytest.raises(TokenRefused):
        await initiative.verify_handoff(context)


async def test_a_call_another_plug_in_made_is_taken_only_for_a_public_endpoint(
    initiative, initiative_stand_in
):
    token = initiative_stand_in.context(
        "plugin.x.write",
        community_ref="c",
        act={"sub": "beyonders-studio.another"},
        actor="member",
        member="member-7",
        initiative_id=12,
    )

    with pytest.raises(TokenRefused, match="another plug-in"):
        await initiative.verify_context(token)
    claims = await initiative.verify_context(token, public={"plugin.x.write"})
    assert (claims.caller, claims.actor, claims.member_ref, claims.initiative_id) == (
        "beyonders-studio.another",
        "member-7",
        "member-7",
        12,
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"act": {"sub": "beyonders-studio.another"}},
        {"act": {"sub": "beyonders-studio.another"}, "actor": "member"},
        {"act": {"sub": "beyonders-studio.another"}, "actor": "installation", "member": "m"},
        {"act": "beyonders-studio.another", "actor": "installation"},
        {"actor": "member", "member": "member-7"},
    ],
)
async def test_a_call_that_does_not_say_whom_it_acts_for_is_refused(
    initiative, initiative_stand_in, claims
):
    token = initiative_stand_in.context("plugin.x.write", community_ref="c", **claims)

    with pytest.raises(TokenRefused):
        await initiative.verify_context(token, public={"plugin.x.write"})


async def test_an_actions_call_names_its_reader_and_item(initiative, initiative_stand_in):
    token = initiative_stand_in.context(
        "plugin.x.write", community_ref="c", viewer="member-3", subject={"type": "task", "id": 41}
    )

    claims = await initiative.verify_context(token)

    assert (claims.caller, claims.member_ref, claims.subject) == (
        None,
        "member-3",
        ContextSubject(type="task", id=41),
    )
    with pytest.raises(TokenRefused, match="subject"):
        await initiative.verify_context(
            initiative_stand_in.context("plugin.x.write", community_ref="c", subject={"id": "41"})
        )


async def test_a_hook_is_not_called_by_another_plug_in(initiative, initiative_stand_in):
    token = initiative_stand_in.lifecycle(
        "erase", community_ref="c", act={"sub": "beyonders-studio.another"}
    )

    with pytest.raises(TokenRefused, match="another plug-in"):
        await initiative.verify_lifecycle(token, hook="erase")


async def test_a_statement_billing_signed_verifies_to_its_plan(initiative, initiative_stand_in):
    statement = initiative_stand_in.statement(
        3, {"plan": "workshop", "active": True, "paid_through": "2026-12-01T00:00:00+00:00"}
    )

    assert initiative.verify_statement(statement) == Statement(
        version=3,
        plan="workshop",
        active=True,
        paid_through=datetime(2026, 12, 1, tzinfo=UTC),
    )


async def test_a_statement_signed_ahead_of_our_clock_verifies(initiative, initiative_stand_in):
    later = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    statement = initiative_stand_in.statement(1, {"plan": "workshop", "active": False}, iat=later)

    assert initiative.verify_statement(statement).active is False


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        (lambda s: None, "malformed"),
        (lambda s: "not.a.jws", "malformed"),
        (lambda s: s.statement(1, {}, key=ed25519.Ed25519PrivateKey.generate()), "signature"),
        (lambda s: s.statement(1, {}, iss="initiative"), "iss"),
        (lambda s: s.statement(1, {}, listing_uid="SOMEONEELSES00"), "listing_uid"),
        (lambda s: s.statement(0, {}), "claims"),
        (lambda s: s.statement(True, {}), "claims"),
        (lambda s: s.statement(1, {"active": "yes"}), "claims"),
        (lambda s: s.statement(1, {"plan": 5}), "claims"),
        (lambda s: s.statement(1, {"paid_through": "2026-12-01T00:00:00"}), "claims"),
        (lambda s: s.statement(1, {"paid_through": "soon"}), "claims"),
        (lambda s: s.sign({}, typ="JWT"), "typ"),
    ],
)
async def test_a_statement_billing_did_not_sign_for_this_app_is_refused(
    initiative, initiative_stand_in, statement, reason
):
    with pytest.raises(StatementRefused) as refused:
        initiative.verify_statement(statement(initiative_stand_in))

    assert refused.value.reason == reason


async def test_a_statement_signed_with_another_algorithm_is_refused(initiative):
    unsigned = "eyJhbGciOiJub25lIiwidHlwIjoiaW5pdGlhdGl2ZS1lbnRpdGxlbWVudCtqd3QifQ.e30."

    with pytest.raises(StatementRefused) as refused:
        initiative.verify_statement(unsigned)

    assert refused.value.reason == "alg"


async def test_without_billings_key_every_statement_is_refused(initiative_stand_in):
    initiative = Initiative(
        api_url=initiative_stand_in.API_URL,
        public_id=initiative_stand_in.public_id,
        listing_uid=initiative_stand_in.listing_uid,
        statement_key_pem=None,
    )
    try:
        with pytest.raises(StatementRefused) as refused:
            initiative.verify_statement(initiative_stand_in.statement(1, {"plan": "workshop"}))
    finally:
        await initiative.aclose()

    assert refused.value.reason == "no_issuer_key"
