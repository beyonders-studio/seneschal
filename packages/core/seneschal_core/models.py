"""The model side of an organization's tables, as
:mod:`~seneschal_core.migrations` makes them: the ``organization_id`` each
carries, an identity key, and references kept within the organization.

Models declare what queries name and the order rows are written in; the
revisions make the tables.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Column, ForeignKeyConstraint, Identity, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlmodel import Field, SQLModel

from seneschal_core.db import REGISTRY_TABLE


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
