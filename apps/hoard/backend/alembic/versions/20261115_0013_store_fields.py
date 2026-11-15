"""A store's fields mapped to what they fill in Hoard, and the last value the
store holds of each field Hoard works out.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "field_owner": ("product", "variant"),
    "field_target": ("allergens", "ingredients", "property"),
}


def _enum(name: str, column: str) -> sa.Column:
    return sa.Column(column, postgresql.ENUM(name=name, create_type=False), nullable=False)


def upgrade() -> None:
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind())

    op.create_table(
        "store_fields",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("store_id", sa.BigInteger(), nullable=False),
        _enum("field_owner", "owner"),
        sa.Column("handle", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        _enum("field_target", "target"),
        sa.Column("property_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "pairs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.CheckConstraint(
            "(target = 'property') = (property_id IS NOT NULL)", name="ck_store_fields_property"
        ),
    )
    isolate("store_fields")
    stamp("store_fields")
    referable("store_fields")
    reference("store_fields", "store_id", "stores", ondelete="CASCADE")
    reference("store_fields", "property_id", "property_definitions", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_store_fields_handle", "store_fields", ["organization_id", "store_id", "owner", "handle"]
    )

    op.create_table(
        "store_field_values",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("field_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("store_value", sa.Text(), nullable=True),
        sa.Column("hoard_value", sa.Text(), nullable=True),
    )
    isolate("store_field_values")
    reference("store_field_values", "field_id", "store_fields", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_store_field_values_owner",
        "store_field_values",
        ["organization_id", "field_id", "owner_id"],
    )


def downgrade() -> None:
    op.drop_table("store_field_values")
    op.drop_table("store_fields")
    for name in ENUMS:
        postgresql.ENUM(name=name).drop(op.get_bind())
