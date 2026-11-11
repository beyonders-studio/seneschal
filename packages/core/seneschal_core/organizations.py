"""The organization registry: one organization per community install.

Initiative names a community to an app by a reference of its own, which every
token it signs carries. The registry trades that reference for the
organization every other row names. The serving login reads the registry and
changes it only through the registry's functions (``migrations.create_registry``).

Where an organization stands decides what may happen in it:

* a suspended or deleted community reaches nothing (:attr:`Organization.available`);
* a community that is read-only or on hold, or whose subscription billing
  says is inactive, reads but changes nothing (:attr:`Organization.read_only`).
  A community holding no statement yet is not held back: billing gates the
  install itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column, DateTime, Identity, text
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from seneschal_core.db import REGISTRY_TABLE


class CommunityStatus(StrEnum):
    """Where a community stands, as Initiative states it on every token."""

    ACTIVE = "active"
    READ_ONLY = "read_only"
    SUSPENDED = "suspended"
    ON_HOLD = "on_hold"
    DELETED = "deleted"


#: Statuses in which the community reaches nothing.
UNAVAILABLE = frozenset({CommunityStatus.SUSPENDED, CommunityStatus.DELETED})
#: Statuses in which the community reads but changes nothing.
READ_ONLY = frozenset({CommunityStatus.READ_ONLY, CommunityStatus.ON_HOLD})


class Organization(SQLModel, table=True):
    """One community install, as the registry holds it."""

    __tablename__ = REGISTRY_TABLE

    id: int = Field(sa_column=Column(BigInteger, Identity(always=True), primary_key=True))
    community_ref: str = Field(unique=True)
    plugin_install_id: int = Field(sa_type=BigInteger)
    community_status: str = Field(
        default=CommunityStatus.ACTIVE, sa_column_kwargs={"server_default": text("'active'")}
    )
    statement_version: int | None = None
    plan: str | None = None
    active: bool | None = None
    paid_through: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        sa_type=DateTime(timezone=True), sa_column_kwargs={"server_default": text("now()")}
    )

    @property
    def available(self) -> bool:
        return self.community_status not in UNAVAILABLE

    @property
    def read_only(self) -> bool:
        return not self.available or self.community_status in READ_ONLY or self.active is False


@dataclass(frozen=True)
class Statement:
    """Billing's statement of a monthly plug-in's plan, verified."""

    version: int
    plan: str | None
    active: bool
    paid_through: datetime | None


async def organization_for(
    session: AsyncSession, *, community_ref: str, plugin_install_id: int
) -> int:
    """The organization for ``community_ref``, made on first sight."""
    organization_id = (
        await session.execute(
            text("SELECT organization_for(:ref, :install)"),
            {"ref": community_ref, "install": plugin_install_id},
        )
    ).scalar_one()
    if organization_id is None:
        raise LookupError("the organization was erased while it was being found")
    return int(organization_id)


async def set_status(session: AsyncSession, organization_id: int, status: CommunityStatus) -> None:
    await session.execute(
        text("SELECT set_organization_status(:id, :status)"),
        {"id": organization_id, "status": status.value},
    )


async def apply_statement(
    session: AsyncSession, organization_id: int, statement: Statement
) -> bool:
    """Keep ``statement`` if it is newer than the one held; answers whether it was."""
    applied = await session.execute(
        text("SELECT apply_organization_statement(:id, :version, :plan, :active, :paid_through)"),
        {
            "id": organization_id,
            "version": statement.version,
            "plan": statement.plan,
            "active": statement.active,
            "paid_through": statement.paid_through,
        },
    )
    return bool(applied.scalar_one())


async def erase(session: AsyncSession, community_ref: str) -> bool:
    """Delete the community's organization and every row naming it; answers
    whether there was one."""
    erased = await session.execute(text("SELECT erase_organization(:ref)"), {"ref": community_ref})
    return bool(erased.scalar_one())


async def get(session: AsyncSession, organization_id: int) -> Organization | None:
    return await session.get(Organization, organization_id)
