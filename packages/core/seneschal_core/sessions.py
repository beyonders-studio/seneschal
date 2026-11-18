"""The app's own session, which a page gets for its handoff.

A page runs in Initiative's frame at the app's own origin. Once it has
exchanged Initiative's one-minute handoff, the app sends it a session of its
own: an EdDSA JWT signed with the app's session key, in an HttpOnly cookie
scoped to the app's origin, which the page's later calls carry. It holds what
the handoff said about the member and where the page was opened, and the
organization the community's reference was traded for. The page's theme and
locale ride with it; they come from the message that carried the handoff, and
say how the page looks rather than what it may reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import Response

from seneschal_core.initiative import ALGORITHM, HandoffClaims

#: How long a session lasts. A page whose session ended asks Initiative for a
#: fresh handoff and exchanges it again.
SESSION_LIFETIME = timedelta(minutes=15)

Theme = Literal["light", "dark"]

#: How a session was made, which says how it may be presented: ``frame`` (a
#: handoff in Initiative's frame) and ``browser`` (a sign-in at the app's own
#: address) travel in the cookie; ``device`` (a sign-in in the app's phone
#: app) and ``mcp`` (an MCP client's sign-in, :mod:`seneschal_core.oauth`)
#: travel as a Bearer token, and never in the cookie.
Via = Literal["frame", "browser", "device", "mcp"]

#: The sessions that travel as a Bearer token.
BEARER_VIAS: frozenset[Via] = frozenset({"device", "mcp"})


@dataclass(frozen=True)
class SessionClaims:
    organization_id: int
    community_ref: str
    member_ref: str
    page: str
    initiative_id: int | None
    admin: bool
    permissions: tuple[str, ...]
    theme: Theme | None
    locale: str | None
    via: Via = "frame"


class SessionRefused(Exception):
    """A session this app did not sign, or one that has ended."""


def load_private_key(pem: str) -> ed25519.Ed25519PrivateKey:
    """An Ed25519 private key from a PKCS#8 PEM, which may be written on one
    line with literal ``\\n`` between its lines."""
    key = serialization.load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("the session key must be an Ed25519 private key")
    return key


class Sessions:
    """Signs and checks the app's sessions, under the app's own name."""

    def __init__(self, *, app: str, private_key_pem: str, cookie_secure: bool) -> None:
        #: The app's own name (``hoard``).
        self.app = app
        self._issuer = app
        self._audience = f"{app}:session"
        self._private_key = load_private_key(private_key_pem)
        self._public_key = self._private_key.public_key()
        self.cookie_name = f"{app}_session"
        self.cookie_secure = cookie_secure

    def mint(
        self,
        handoff: HandoffClaims,
        *,
        organization_id: int,
        theme: Theme | None,
        locale: str | None,
        not_after: datetime | None = None,
        via: Via = "frame",
    ) -> str:
        """A session for what ``handoff`` says, for :data:`SESSION_LIFETIME`
        or until ``not_after``, whichever comes first: a session made from a
        sign-in never outlives the ID token it was made from."""
        now = datetime.now(UTC)
        expires = now + SESSION_LIFETIME
        if not_after is not None:
            expires = min(expires, not_after)
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": expires,
            "sub": handoff.member_ref,
            "organization_id": organization_id,
            "community_ref": handoff.community_ref,
            "page": handoff.page,
            "initiative_id": handoff.initiative_id,
            "admin": handoff.admin,
            "permissions": list(handoff.permissions),
            "theme": theme,
            "locale": locale,
            "via": via,
        }
        return jwt.encode(payload, self._private_key, algorithm=ALGORITHM)

    def set_cookie(self, response: Response, token: str, *, framed: bool) -> None:
        """Send the session in its cookie, kept as long as the session lasts.

        A page Initiative frames is on another site in production, where only
        a secure cookie marked ``None`` is sent. A page opened at the app's own
        address is the top-level site, and its cookie is ``Lax``: it goes with
        the page's own calls and never with another site's."""
        expires = jwt.decode(token, options={"verify_signature": False})["exp"]
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=max(0, int(expires - datetime.now(UTC).timestamp())),
            httponly=True,
            secure=self.cookie_secure,
            samesite="none" if framed and self.cookie_secure else "lax",
            path="/",
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(self.cookie_name, httponly=True, secure=self.cookie_secure, path="/")

    def verify(self, token: str) -> SessionClaims:
        try:
            payload = jwt.decode(
                token,
                self._public_key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "organization_id"]},
            )
        except jwt.PyJWTError as exc:
            raise SessionRefused(str(exc)) from exc
        return SessionClaims(
            organization_id=int(payload["organization_id"]),
            community_ref=str(payload["community_ref"]),
            member_ref=str(payload["sub"]),
            page=str(payload["page"]),
            initiative_id=payload["initiative_id"],
            admin=payload["admin"] is True,
            permissions=tuple(payload["permissions"]),
            theme=payload["theme"],
            locale=payload["locale"],
            # A session from before ``via`` was a frame's.
            via=payload.get("via", "frame"),
        )
