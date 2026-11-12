"""Trace and recall: hold reasons and the holds on lots, with the adjustments
that discard them; files linked to lots, products, materials and bases; which
lot of a category goes first; cure times; the Food Traceability List flag;
a location's contact; the rules an organization keeps records for; and the EU
fragrance allergens of materials and lots.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, quote, referable, reference, stamp

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Each new type and its values.
ENUMS = {
    "hold_end": ("released", "discarded"),
    "picking": ("earliest_expiry", "first_in", "last_in", "recorded"),
}

#: The new tables in the order they are made, each saying who made and
#: changed its records.
TABLES = ("hold_reasons", "holds", "document_links")

#: The hold reasons every organization starts with.
DEFAULT_HOLD_REASONS = ("curing", "awaiting_results", "suspect", "recall")

#: The rules an organization may keep records for.
REGIMES = (
    "us_one_up_one_back",
    "us_food_traceability",
    "us_organic",
    "us_cosmetics",
    "eu_food",
    "eu_cosmetics",
    "canada_food",
)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _ref(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _set_null(table: str, column: str, parent: str) -> None:
    """``table.column`` references a row of ``parent`` in the same
    organization, and is cleared when that row is deleted."""
    op.execute(
        f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(f'fk_{table}_{column}')} "
        f"FOREIGN KEY (organization_id, {quote(column)}) "
        f"REFERENCES {quote(parent)} (organization_id, id) ON DELETE SET NULL ({quote(column)})"
    )


def upgrade() -> None:
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind())

    columns: dict[str, list] = {
        "hold_reasons": [
            _id(),
            sa.Column("key", sa.Text(), nullable=True),
            sa.Column("name", sa.Text(), nullable=True),
            sa.CheckConstraint("name IS NOT NULL OR key IS NOT NULL", name="ck_hold_reasons_name"),
        ],
        "holds": [
            _id(),
            _ref("lot_id"),
            _ref("reason_id"),
            sa.Column("ready_on", sa.Date(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            _ref("run_id", nullable=True),
            _ref("order_id", nullable=True),
            _ref("from_hold_id", nullable=True),
            sa.Column("ended_on", sa.Date(), nullable=True),
            sa.Column("ended", postgresql.ENUM(name="hold_end", create_type=False), nullable=True),
            sa.Column("ready_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("(ended IS NULL) = (ended_on IS NULL)", name="ck_holds_ended"),
        ],
        "document_links": [
            _id(),
            _ref("file_id"),
            _ref("lot_id", nullable=True),
            _ref("product_id", nullable=True),
            _ref("material_id", nullable=True),
            _ref("base_id", nullable=True),
            sa.CheckConstraint(
                "num_nonnulls(lot_id, product_id, material_id, base_id) = 1",
                name="ck_document_links_record",
            ),
        ],
    }
    for table in TABLES:
        op.create_table(table, *columns[table])
        isolate(table)
        stamp(table)
    for table in ("hold_reasons", "holds"):
        referable(table)

    op.create_unique_constraint("uq_hold_reasons_name", "hold_reasons", ["organization_id", "name"])
    op.create_unique_constraint("uq_hold_reasons_key", "hold_reasons", ["organization_id", "key"])

    reference("holds", "lot_id", "lots", ondelete="CASCADE")
    reference("holds", "reason_id", "hold_reasons")
    _set_null("holds", "run_id", "production_runs")
    _set_null("holds", "order_id", "orders")
    _set_null("holds", "from_hold_id", "holds")
    op.create_index("ix_holds_lot", "holds", ["organization_id", "lot_id"])
    op.create_index(
        "uq_holds_open",
        "holds",
        ["organization_id", "lot_id"],
        unique=True,
        postgresql_where=sa.text("ended IS NULL"),
    )

    for column, parent in (
        ("lot_id", "lots"),
        ("product_id", "products"),
        ("material_id", "materials"),
        ("base_id", "bases"),
    ):
        reference("document_links", column, parent, ondelete="CASCADE")
        op.create_unique_constraint(
            f"uq_document_links_{column.removesuffix('_id')}",
            "document_links",
            ["organization_id", column, "file_id"],
        )

    op.add_column("adjustments", _ref("hold_id", nullable=True))
    _set_null("adjustments", "hold_id", "holds")
    op.create_check_constraint(
        "ck_adjustments_hold", "adjustments", "hold_id IS NULL OR kind = 'manual'"
    )

    op.add_column(
        "categories",
        sa.Column(
            "picking",
            postgresql.ENUM(name="picking", create_type=False),
            nullable=False,
            server_default="earliest_expiry",
        ),
    )
    for table in ("bases", "products"):
        op.add_column(table, sa.Column("cure_days", sa.Integer(), nullable=True))
        op.create_check_constraint(f"ck_{table}_cure_days", table, "cure_days > 0")
    for column in ("traceability_list", "rinse_off"):
        op.add_column(
            "products", sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.add_column("locations", _ref("contact_id", nullable=True))
    op.add_column(
        "settings",
        sa.Column(
            "regimes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_check_constraint(
        "ck_settings_regimes",
        "settings",
        "regimes <@ ARRAY[{}]::text[]".format(", ".join(f"'{regime}'" for regime in REGIMES)),
    )
    for table in ("materials", "lots"):
        op.add_column(table, sa.Column("fragrance_allergens", postgresql.JSONB(), nullable=True))
        op.create_check_constraint(
            f"ck_{table}_fragrance_allergens",
            table,
            "jsonb_typeof(fragrance_allergens) = 'object'",
        )

    # Every organization that has its default adjustment reasons starts with
    # the default hold reasons too; one that has none yet is given both
    # together.
    defaults = ", ".join(f"'{key}'" for key in DEFAULT_HOLD_REASONS)
    op.execute("ALTER TABLE hold_reasons NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO hold_reasons (organization_id, key) "
        "SELECT DISTINCT reasons.organization_id, defaults.key "
        f"FROM adjustment_reasons AS reasons, unnest(ARRAY[{defaults}]) AS defaults (key) "
        "WHERE reasons.key IS NOT NULL ON CONFLICT DO NOTHING"
    )
    op.execute("ALTER TABLE hold_reasons FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in ("lots", "materials"):
        op.drop_column(table, "fragrance_allergens")
    op.drop_column("settings", "regimes")
    op.drop_column("locations", "contact_id")
    for column in ("rinse_off", "traceability_list"):
        op.drop_column("products", column)
    for table in ("products", "bases"):
        op.drop_column(table, "cure_days")
    op.drop_column("categories", "picking")
    op.drop_column("adjustments", "hold_id")

    for table in reversed(TABLES):
        op.drop_table(table)
    for name in reversed(ENUMS):
        postgresql.ENUM(name=name).drop(op.get_bind())
