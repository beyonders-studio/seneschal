"""The serving login's connection to an app's database, and its isolation.

An app connects with two logins. The owner runs migrations and owns every
table; it never serves a request. The serving login serves requests: it is not
a superuser, does not bypass row security, and owns no table, which
:func:`verify_serving_login` checks before the app starts.

Every table but the organization registry holds rows for one organization each,
under one policy: a row is visible and writable only while the transaction's
``app.organization_id`` names its organization. A session opened for an
organization sets that value transaction-locally at the start of every
transaction it begins, so it never outlasts the transaction on a pooled
connection. A session opened for none leaves it unset, and reads and writes no
organization's rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine
from sqlalchemy.orm import SessionTransaction
from sqlmodel import Session
from sqlmodel.ext.asyncio.session import AsyncSession

#: The setting each organization's policy reads.
ORGANIZATION_SETTING = "app.organization_id"
#: The registry: one row per community install, the one table without the policy.
REGISTRY_TABLE = "organizations"
#: The one policy every other table carries.
POLICY_NAME = "organization_isolation"
#: The policy's condition. Unset, the setting reads as NULL or as an empty
#: string, and either matches no row.
POLICY_CONDITION = (
    f"organization_id = NULLIF(current_setting('{ORGANIZATION_SETTING}', true), '')::bigint"
)

#: Where a session keeps the organization it was opened for.
_ORGANIZATION_KEY = "organization_id"


class _OrganizationSyncSession(Session):
    """The synchronous session behind :class:`OrganizationSession`."""


@event.listens_for(_OrganizationSyncSession, "after_begin")
def _set_organization(
    session: _OrganizationSyncSession, _transaction: SessionTransaction, connection: Connection
) -> None:
    organization_id = session.info.get(_ORGANIZATION_KEY)
    if organization_id is None:
        return
    connection.execute(
        text("SELECT set_config(:setting, :value, true)"),
        {"setting": ORGANIZATION_SETTING, "value": str(int(organization_id))},
    )


class OrganizationSession(AsyncSession):
    """A session that sets its organization at the start of every transaction."""

    sync_session_class = _OrganizationSyncSession


def create_async_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """An asyncpg engine for the Postgres database ``url`` names, whatever its driver."""
    return _create_async_engine(make_url(url).set(drivername="postgresql+asyncpg"), **kwargs)


class Database:
    """The serving login's engine and the sessions opened on it."""

    def __init__(self, url: str, **engine_options: Any) -> None:
        self.engine = create_async_engine(url, pool_pre_ping=True, **engine_options)
        self._sessions = async_sessionmaker(
            self.engine, class_=OrganizationSession, expire_on_commit=False, autoflush=False
        )

    def session(self) -> OrganizationSession:
        """A session for no organization: it reads the registry and calls the
        registry's functions, and reaches no organization's rows."""
        return self._sessions()

    def organization_session(self, organization_id: int) -> OrganizationSession:
        """A session for one organization, whose rows alone it reaches."""
        return self._sessions(info={_ORGANIZATION_KEY: int(organization_id)})

    async def dispose(self) -> None:
        await self.engine.dispose()


class ServingLoginRefused(RuntimeError):
    """The serving login is a superuser, bypasses row security, or owns a table."""


async def verify_serving_login(engine: AsyncEngine) -> None:
    """Refuse a serving login that is a superuser, bypasses row security, or
    owns (or is a member of the owner of) any table in the database."""
    async with engine.connect() as connection:
        login, superuser, bypass = (
            await connection.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
        if superuser:
            raise ServingLoginRefused(f"the serving login {login} is a superuser")
        if bypass:
            raise ServingLoginRefused(f"the serving login {login} bypasses row security")
        owned = await _owned_tables(connection)
        if owned:
            raise ServingLoginRefused(
                f"the serving login {login} owns tables: {', '.join(sorted(owned))}"
            )


async def _owned_tables(connection: AsyncConnection) -> list[str]:
    rows = await connection.execute(
        text(
            """
            SELECT n.nspname || '.' || c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind IN ('r', 'p')
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
               AND n.nspname NOT LIKE 'pg\\_toast%'
               AND n.nspname NOT LIKE 'pg\\_temp\\_%'
               AND pg_has_role(current_user, c.relowner, 'MEMBER')
            """
        )
    )
    return list(rows.scalars())
