"""Hoard's migrations, which run as the owner login."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from hoard.core.config import BACKEND_ROOT


def alembic_config(owner_url: str | None = None, serving_login: str | None = None) -> Config:
    """Alembic's configuration for Hoard. Without arguments, the migration
    environment reads both logins from the settings."""
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.attributes["owner_url"] = owner_url
    config.attributes["serving_login"] = serving_login
    return config


def upgrade(owner_url: str, serving_login: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(owner_url, serving_login), revision)


def downgrade(owner_url: str, serving_login: str, revision: str) -> None:
    command.downgrade(alembic_config(owner_url, serving_login), revision)
