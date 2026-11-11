"""Alembic's environment for Hoard: migrations run as the owner login, and
grant to the serving login (``seneschal_core.migrations``)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from hoard.core.config import DatabaseSettings
from hoard.models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _logins() -> tuple[str, str]:
    """The owner's URL, and the serving login's name: as given, or from the settings."""
    owner_url = config.attributes.get("owner_url")
    serving_login = config.attributes.get("serving_login")
    if owner_url and serving_login:
        return owner_url, serving_login
    settings = DatabaseSettings()
    serving_login = make_url(settings.database_url_app).username
    if not serving_login:
        raise SystemExit("HOARD_DATABASE_URL_APP names no login")
    return settings.database_url, serving_login


def run_migrations() -> None:
    owner_url, serving_login = _logins()
    engine = create_engine(
        make_url(owner_url).set(drivername="postgresql+psycopg"), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=metadata, serving_login=serving_login
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
