"""The properties people add to materials, products and lots, each record's
values of them, and how each kind of record is listed and shown on its page.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "record_kind": ("material", "product", "lot"),
    "property_type": (
        "text",
        "number",
        "checkbox",
        "date",
        "datetime",
        "url",
        "select",
        "multi_select",
        "user_reference",
        "currency",
        "scale",
        "counter",
        "file",
        "signature",
    ),
    "layout_view": ("table", "detail"),
}


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _enum(column: str, name: str) -> sa.Column:
    return sa.Column(column, postgresql.ENUM(name=name, create_type=False), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind)

    op.create_table(
        "property_definitions",
        _id(),
        _enum("kind", "record_kind"),
        sa.Column("name", sa.Text(), nullable=False),
        _enum("type", "property_type"),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("counter", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "(type = 'currency') = (currency IS NOT NULL)",
            name="ck_property_definitions_currency",
        ),
    )
    isolate("property_definitions")
    stamp("property_definitions")
    referable("property_definitions")
    op.create_unique_constraint(
        "uq_property_definitions_name", "property_definitions", ["organization_id", "kind", "name"]
    )

    records = ("material", "product", "lot")
    op.create_table(
        "property_values",
        _id(),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        *(sa.Column(f"{record}_id", sa.BigInteger(), nullable=True) for record in records),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(material_id, product_id, lot_id) = 1", name="ck_property_values_record"
        ),
    )
    isolate("property_values")
    stamp("property_values")
    reference("property_values", "definition_id", "property_definitions", ondelete="CASCADE")
    for record in records:
        reference("property_values", f"{record}_id", f"{record}s", ondelete="CASCADE")
        op.create_unique_constraint(
            f"uq_property_values_{record}",
            "property_values",
            ["organization_id", f"{record}_id", "definition_id"],
        )

    op.create_table(
        "layouts",
        _id(),
        _enum("kind", "record_kind"),
        _enum("view", "layout_view"),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
    )
    isolate("layouts")
    stamp("layouts")
    op.create_unique_constraint("uq_layouts_view", "layouts", ["organization_id", "kind", "view"])


def downgrade() -> None:
    for table in ("layouts", "property_values", "property_definitions"):
        op.drop_table(table)
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind())
