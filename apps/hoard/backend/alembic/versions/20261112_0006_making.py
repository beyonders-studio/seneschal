"""Making: bases, recipes and production runs; the tasks that track runs and
purchases, and the outbox of events Hoard sends; allergens and ingredient
names; bundles, the word for a base, and the initiative a purchase was opened
from; properties a setup adds, and on bases and runs.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core.migrations import isolate, quote, referable, reference, stamp

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The values each existing type gains.
ENUM_VALUES = {
    "item_kind": ("base",),
    "category_kind": ("base",),
    "adjustment_kind": ("production",),
    "record_kind": ("base", "run"),
}

ALLERGENS_CHECK = (
    "allergens <@ ARRAY['celery', 'crustaceans', 'eggs', 'fish', 'gluten', 'lupin', 'milk', "
    "'molluscs', 'mustard', 'peanuts', 'sesame', 'soybeans', 'sulphites', 'tree_nuts', "
    "'wheat']::text[]"
)

#: Each new table in the order it is made, and whether other tables reference it.
TABLES = {
    "bases": True,
    "recipes": True,
    "recipe_lines": False,
    "production_runs": True,
    "production_run_lines": True,
    "task_links": False,
    "event_outbox": False,
}


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _ref(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def _amount(name: str, *, default: str | None = "0") -> sa.Column:
    return sa.Column(name, sa.Numeric(20, 6), nullable=default is None, server_default=default)


def _flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def _allergens() -> sa.Column:
    return sa.Column("allergens", postgresql.ARRAY(sa.Text()), nullable=True)


def _set_null(table: str, column: str, parent: str) -> None:
    """``table.column`` references a row of ``parent`` in the same
    organization, and is cleared when that row is deleted."""
    op.execute(
        f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(f'fk_{table}_{column}')} "
        f"FOREIGN KEY (organization_id, {quote(column)}) "
        f"REFERENCES {quote(parent)} (organization_id, id) ON DELETE SET NULL ({quote(column)})"
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, values in ENUM_VALUES.items():
            for value in values:
                op.execute(f"ALTER TYPE {name} ADD VALUE IF NOT EXISTS '{value}'")
    postgresql.ENUM("not_started", "in_progress", "completed", name="run_status").create(
        op.get_bind()
    )
    referable("variations")

    columns: dict[str, list] = {
        "bases": [
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("sku", sa.Text(), nullable=True),
            _ref("tracking_unit_id"),
            _ref("category_id", nullable=True),
            _amount("low_stock_limit", default=None),
        ],
        "recipes": [
            _id(),
            _ref("variation_id", nullable=True),
            _ref("base_id", nullable=True),
            _ref("product_id", nullable=True),
            _amount("batch_quantity", default="1"),
            sa.Column("minutes", sa.Integer(), nullable=True),
            sa.CheckConstraint(
                "num_nonnulls(variation_id, base_id, product_id) = 1", name="ck_recipes_output"
            ),
            sa.CheckConstraint(
                "batch_quantity > 0 AND (minutes IS NULL OR minutes >= 0)",
                name="ck_recipes_batch",
            ),
        ],
        "recipe_lines": [
            _id(),
            _ref("recipe_id"),
            _ref("item_id"),
            _amount("quantity"),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.CheckConstraint("quantity > 0", name="ck_recipe_lines_quantity"),
        ],
        "production_runs": [
            _id(),
            sa.Column("code", sa.Text(), nullable=False),
            _ref("item_id"),
            _ref("recipe_id", nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(name="run_status", create_type=False),
                nullable=False,
                server_default="not_started",
            ),
            _ref("location_id"),
            _ref("initiative_id", nullable=True),
            _amount("planned_batches", default="1"),
            _amount("actual_quantity", default=None),
            sa.Column("labour_minutes", sa.Integer(), nullable=True),
            sa.Column("assignee_ref", sa.Text(), nullable=True),
            sa.Column("started_on", sa.Date(), nullable=True),
            sa.Column("deadline_on", sa.Date(), nullable=True),
            sa.Column("completed_on", sa.Date(), nullable=True),
            sa.Column("expires_on", sa.Date(), nullable=True),
            _ref("output_lot_id", nullable=True),
            _flag("uncosted"),
            sa.CheckConstraint(
                "planned_batches > 0 AND actual_quantity >= 0 AND labour_minutes >= 0",
                name="ck_production_runs_amounts",
            ),
            sa.CheckConstraint(
                "status <> 'completed' "
                "OR (completed_on IS NOT NULL AND actual_quantity IS NOT NULL)",
                name="ck_production_runs_completed",
            ),
        ],
        "production_run_lines": [
            _id(),
            _ref("run_id"),
            _ref("item_id"),
            _ref("lot_id", nullable=True),
            _amount("quantity"),
            _flag("assumed"),
            sa.CheckConstraint("quantity > 0", name="ck_production_run_lines_quantity"),
        ],
        "task_links": [
            _id(),
            _ref("task_id"),
            _ref("run_id", nullable=True),
            _ref("purchase_id", nullable=True),
            sa.CheckConstraint(
                "num_nonnulls(run_id, purchase_id) = 1", name="ck_task_links_record"
            ),
        ],
        "event_outbox": [
            _id(),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            _ref("initiative_id", nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "next_attempt_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint("attempts >= 0", name="ck_event_outbox_attempts"),
        ],
    }
    for table, referenced in TABLES.items():
        op.create_table(table, *columns[table])
        isolate(table)
        if table != "event_outbox":
            stamp(table)
        if referenced:
            referable(table)

    reference("bases", "id", "items", ondelete="CASCADE")
    reference("bases", "tracking_unit_id", "units")
    reference("bases", "category_id", "categories")
    reference("recipes", "variation_id", "variations", ondelete="CASCADE")
    reference("recipes", "base_id", "bases", ondelete="CASCADE")
    reference("recipes", "product_id", "products", ondelete="CASCADE")
    for output in ("variation", "base", "product"):
        op.create_unique_constraint(
            f"uq_recipes_{output}", "recipes", ["organization_id", f"{output}_id"]
        )
    reference("recipe_lines", "recipe_id", "recipes", ondelete="CASCADE")
    reference("recipe_lines", "item_id", "items")
    op.create_unique_constraint(
        "uq_recipe_lines_item", "recipe_lines", ["organization_id", "recipe_id", "item_id"]
    )
    reference("production_runs", "item_id", "items", ondelete="CASCADE")
    _set_null("production_runs", "recipe_id", "recipes")
    reference("production_runs", "location_id", "locations")
    _set_null("production_runs", "output_lot_id", "lots")
    op.create_unique_constraint(
        "uq_production_runs_code", "production_runs", ["organization_id", "code"]
    )
    reference("production_run_lines", "run_id", "production_runs", ondelete="CASCADE")
    reference("production_run_lines", "item_id", "items")
    _set_null("production_run_lines", "lot_id", "lots")
    op.create_index(
        "ix_production_run_lines_run", "production_run_lines", ["organization_id", "run_id"]
    )
    reference("task_links", "run_id", "production_runs", ondelete="CASCADE")
    reference("task_links", "purchase_id", "purchases", ondelete="CASCADE")
    for column in ("task", "run", "purchase"):
        op.create_unique_constraint(
            f"uq_task_links_{column}", "task_links", ["organization_id", f"{column}_id"]
        )
    op.create_index("ix_event_outbox_due", "event_outbox", ["organization_id", "next_attempt_at"])

    op.add_column("items", _flag("low_stock"))
    op.add_column("products", _flag("bundle"))
    op.add_column("products", _flag("low_stock"))
    op.add_column("materials", sa.Column("ingredient_name", sa.Text(), nullable=True))
    for table in ("materials", "lots"):
        op.add_column(table, _allergens())
        op.create_check_constraint(f"ck_{table}_allergens", table, ALLERGENS_CHECK)
    op.add_column("settings", sa.Column("base_word", sa.Text(), nullable=True))
    op.add_column("purchases", _ref("initiative_id", nullable=True))

    op.add_column("adjustments", _ref("production_run_id", nullable=True))
    op.add_column("adjustments", _ref("production_run_line_id", nullable=True))
    reference("adjustments", "production_run_id", "production_runs", ondelete="CASCADE")
    reference("adjustments", "production_run_line_id", "production_run_lines", ondelete="CASCADE")
    op.create_unique_constraint(
        "uq_adjustments_production_run_line_id", "adjustments", ["production_run_line_id"]
    )
    op.create_index(
        "uq_adjustments_production_output",
        "adjustments",
        ["production_run_id"],
        unique=True,
        postgresql_where=sa.text("production_run_line_id IS NULL"),
    )
    op.create_index(
        "ix_adjustments_production_run", "adjustments", ["organization_id", "production_run_id"]
    )
    op.create_check_constraint(
        "ck_adjustments_production",
        "adjustments",
        "(kind = 'production') = (production_run_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_adjustments_production_line",
        "adjustments",
        "production_run_line_id IS NULL OR production_run_id IS NOT NULL",
    )

    op.add_column("property_definitions", sa.Column("key", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_property_definitions_key", "property_definitions", ["organization_id", "kind", "key"]
    )
    for record, parent in (("base", "bases"), ("run", "production_runs")):
        op.add_column("property_values", _ref(f"{record}_id", nullable=True))
        reference("property_values", f"{record}_id", parent, ondelete="CASCADE")
        op.create_unique_constraint(
            f"uq_property_values_{record}",
            "property_values",
            ["organization_id", f"{record}_id", "definition_id"],
        )
    op.drop_constraint("ck_property_values_record", "property_values")
    op.create_check_constraint(
        "ck_property_values_record",
        "property_values",
        "num_nonnulls(material_id, product_id, lot_id, base_id, run_id) = 1",
    )


def _delete(table: str, condition: str) -> None:
    """Delete ``table``'s rows that meet ``condition``, in every organization."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DELETE FROM {table} WHERE {condition}")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Postgres keeps a type's values once added: the rows holding the new ones
    # go, with what references them.
    _delete("adjustments", "kind = 'production'")
    _delete("property_values", "base_id IS NOT NULL OR run_id IS NOT NULL")

    op.drop_constraint("ck_property_values_record", "property_values")
    op.create_check_constraint(
        "ck_property_values_record",
        "property_values",
        "num_nonnulls(material_id, product_id, lot_id) = 1",
    )
    for record in ("run", "base"):
        op.drop_column("property_values", f"{record}_id")
    op.drop_constraint("uq_property_definitions_key", "property_definitions")
    op.drop_column("property_definitions", "key")

    op.drop_constraint("ck_adjustments_production_line", "adjustments")
    op.drop_constraint("ck_adjustments_production", "adjustments")
    op.drop_index("ix_adjustments_production_run", "adjustments")
    op.drop_index("uq_adjustments_production_output", "adjustments")
    op.drop_column("adjustments", "production_run_line_id")
    op.drop_column("adjustments", "production_run_id")

    op.drop_column("purchases", "initiative_id")
    op.drop_column("settings", "base_word")
    for table in ("lots", "materials"):
        op.drop_column(table, "allergens")
    op.drop_column("materials", "ingredient_name")
    op.drop_column("products", "low_stock")
    op.drop_column("products", "bundle")
    op.drop_column("items", "low_stock")

    for table in reversed(TABLES):
        op.drop_table(table)
    op.drop_constraint("uq_variations_organization_id_id", "variations")
    postgresql.ENUM(name="run_status").drop(op.get_bind())

    _delete("property_definitions", "kind IN ('base', 'run')")
    _delete("layouts", "kind IN ('base', 'run')")
    _delete("items", "kind = 'base'")
    _delete("stocktakes", "kind = 'base'")
    _delete("categories", "kind = 'base'")
