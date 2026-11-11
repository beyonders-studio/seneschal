"""Hoard's API schema, which the frontend generates its client from."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from hoard.app import create_app
from hoard.core.config import Settings


def openapi_schema() -> dict[str, Any]:
    """The schema of Hoard's API, built with no deployment behind it: nothing
    connects, and the session key is thrown away."""
    key = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    settings = Settings(
        database_url="postgresql://localhost/hoard",
        database_url_app="postgresql://localhost/hoard",
        initiative_api_url="http://localhost/api/v1",
        session_private_key_pem=key.decode(),
    )
    return create_app(settings).openapi()
