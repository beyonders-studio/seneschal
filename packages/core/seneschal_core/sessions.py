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

from seneschal_core.initiative import ALGORITHM, HandoffClaims

#: How long a session lasts. A page whose session ended asks Initiative for a
#: fresh handoff and exchanges it again.
SESSION_LIFETIME = timedelta(minutes=15)

Theme = Literal["light", "dark"]


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
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "iat": int(now.timestamp()),
            "exp": now + SESSION_LIFETIME,
            "sub": handoff.member_ref,
            "organization_id": organization_id,
            "community_ref": handoff.community_ref,
            "page": handoff.page,
            "initiative_id": handoff.initiative_id,
            "admin": handoff.admin,
            "permissions": list(handoff.permissions),
            "theme": theme,
            "locale": locale,
        }
        return jwt.encode(payload, self._private_key, algorithm=ALGORITHM)

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
        )
