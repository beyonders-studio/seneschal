"""The model side of an organization's tables, as
:mod:`~seneschal_core.migrations` makes them: the ``organization_id`` each
carries, an identity key, references kept within the organization, and who
made and last changed a record, and when.

Models declare what queries name and the order rows are written in; the
revisions make the tables.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    UniqueConstraint,
    event,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Session
from sqlmodel import Field, SQLModel

from seneschal_core.db import ACTOR_KEY, REGISTRY_TABLE, OrganizationSession


class OrganizationRow(SQLModel):
    """A row of one organization: the column :func:`~seneschal_core.migrations.isolate`
    adds. Whatever adds a row sets it, from its session's organization."""

    organization_id: int = Field(
        default=None,
        sa_type=BigInteger,
        foreign_key=f"{REGISTRY_TABLE}.id",
        ondelete="CASCADE",
        index=True,
    )


class Stamped(SQLModel):
    """A record that says who made it and when, and who last changed it and
    when: the columns :func:`~seneschal_core.migrations.stamp` adds.

    "Who" is a member's pairwise reference, never a name, so a member erased
    leaves what they did; None is the app itself. A write session fills them
    in as it writes a record, from its actor."""

    created_at: datetime = Field(default=None, sa_type=DateTime(timezone=True))
    created_by: str | None = None
    updated_at: datetime = Field(default=None, sa_type=DateTime(timezone=True))
    updated_by: str | None = None


@event.listens_for(OrganizationSession.sync_session_class, "before_flush")
def _stamp(session: Session, _context: Any, _instances: Any) -> None:
    if ACTOR_KEY not in session.info:
        return
    actor, now = session.info[ACTOR_KEY], datetime.now(UTC)
    for row in session.new:
        if isinstance(row, Stamped):
            row.created_at = row.updated_at = now
            row.created_by = row.updated_by = actor
    for row in session.dirty:
        if isinstance(row, Stamped) and session.is_modified(row):
            row.updated_at, row.updated_by = now, actor


def identity() -> Any:
    """An identity primary key, numbered by the database on insert."""
    return Field(
        default=None, sa_column=Column(BigInteger, Identity(always=True), primary_key=True)
    )


def enum_column(enum: type[StrEnum], name: str, **kwargs: Any) -> Column:
    """A column of the Postgres enum type ``name``, holding ``enum``'s values."""
    return Column(
        SqlEnum(enum, name=name, values_callable=lambda members: [m.value for m in members]),
        nullable=False,
        **kwargs,
    )


def referable(table: str) -> UniqueConstraint:
    """The key :func:`~seneschal_core.migrations.referable` makes."""
    return UniqueConstraint("organization_id", "id", name=f"uq_{table}_organization_id_id")


def reference(
    table: str, column: str, parent: str, *, ondelete: str | None = None
) -> ForeignKeyConstraint:
    """The foreign key :func:`~seneschal_core.migrations.reference` makes."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{parent}.organization_id", f"{parent}.id"],
        name=f"fk_{table}_{column}",
        ondelete=ondelete,
    )
