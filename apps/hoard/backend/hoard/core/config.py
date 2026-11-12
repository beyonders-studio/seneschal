"""Hoard's settings, from the environment (``HOARD_`` names) or the
repository's ``.env`` in development."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from seneschal_core.sessions import load_private_key

#: ``apps/hoard/backend``, which the image lays out the same way.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
#: The repository, whose ``.env`` development reads.
_REPOSITORY_ENV = BACKEND_ROOT.parents[2] / ".env"


class DatabaseSettings(BaseSettings):
    """The two logins, which migrations read on their own."""

    model_config = SettingsConfigDict(
        env_prefix="HOARD_", env_file=_REPOSITORY_ENV, extra="ignore", populate_by_name=True
    )

    #: The owner login, which runs migrations and never serves a request.
    database_url: str
    #: The serving login.
    database_url_app: str


class Settings(DatabaseSettings):
    #: Initiative's API, where its plug-in platform publishes its keys:
    #: ``https://initiative.example.com/api/v1``, or its address inside the deployment.
    initiative_api_url: str
    #: Initiative's API as stores reach it, under which Initiative receives
    #: Hoard's webhooks: ``https://initiative.example.com/api/v1``. Unset, it
    #: is :attr:`initiative_api_url`.
    initiative_public_url: str | None = None
    #: Where people's browsers load Hoard's pages. An ``https`` origin marks the
    #: session cookie secure and sends it to the pages Initiative frames.
    app_origin: str = "http://localhost:8300"
    #: The Ed25519 private key (PKCS#8 PEM) Hoard signs its sessions with; one
    #: line with literal ``\n`` between the PEM's lines will do.
    session_private_key_pem: str
    #: The Ed25519 private key (PKCS#8 PEM) Hoard calls Initiative's plug-in API
    #: with, whose public half the deployment registers for Hoard; one line with
    #: literal ``\n`` between the PEM's lines will do. Unset, Initiative is not
    #: called: members go unnamed and contacts unread.
    plugin_private_key_pem: str | None = None
    #: Billing's statement key (SPKI PEM), from the deployment. Unset, every
    #: statement is refused. Its name is the plug-in platform's, without the prefix.
    entitlement_issuer_key_pem: str | None = Field(
        default=None, validation_alias="INITIATIVE_ENTITLEMENT_ISSUER_KEY_PEM"
    )
    #: The built pages, served from Hoard's own origin.
    static_dir: Path = BACKEND_ROOT / "static"

    @field_validator("session_private_key_pem", "plugin_private_key_pem")
    @classmethod
    def _signing_key(cls, value: str | None) -> str | None:
        if value is not None:
            load_private_key(value)
        return value

    @field_validator(
        "plugin_private_key_pem",
        "entitlement_issuer_key_pem",
        "initiative_public_url",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return value.strip() or None if isinstance(value, str) else value

    @property
    def public_api_url(self) -> str:
        """Initiative's API as stores reach it."""
        return self.initiative_public_url or self.initiative_api_url

    @property
    def cookie_secure(self) -> bool:
        return self.app_origin.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
