"""Types of material, base and product, each with its own page; a category's
default type; and a product's own ingredient name, allergens and fragrance
allergens.

Every organization is given each kind's default type, in the kind's first
shape, and every material, base and product takes its kind's default. A
detail layout someone changed for materials, bases or products becomes the
default type's page, so each page stays as it was.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from seneschal_core import search
from seneschal_core.migrations import isolate, referable, reference, stamp

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Each kind's shapes as this revision knows them, the first its default type's.
SHAPES = {
    "material": ("ingredient", "packaging"),
    "base": ("base",),
    "product": ("made", "bought", "bundle"),
}
#: The tables given a type.
TYPED = ("materials", "bases", "products")
#: The tables this revision writes rows of, with their row security lifted
#: while it does.
WRITTEN = ("item_types", *TYPED, "layouts")

ALLERGENS = (
    "celery",
    "crustaceans",
    "eggs",
    "fish",
    "gluten",
    "lupin",
    "milk",
    "molluscs",
    "mustard",
    "peanuts",
    "sesame",
    "soybeans",
    "sulphites",
    "tree_nuts",
    "wheat",
)


def _lift(*statements: str) -> None:
    """Run ``statements`` with the row security of :data:`WRITTEN` lifted."""
    for table in WRITTEN:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    try:
        for statement in statements:
            op.execute(statement)
    finally:
        for table in WRITTEN:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _set_null_reference(table: str, column: str, parent: str) -> None:
    """``table.column`` references a row of ``parent``, and is cleared when it goes."""
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{column} "
        f"FOREIGN KEY (organization_id, {column}) "
        f"REFERENCES {parent} (organization_id, id) ON DELETE SET NULL ({column})"
    )


def upgrade() -> None:
    kind = postgresql.ENUM(name="category_kind", create_type=False)
    op.create_table(
        "item_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("kind", kind, nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("shape", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("name IS NOT NULL OR is_default", name="ck_item_types_name"),
        sa.CheckConstraint(
            "(kind::text, shape) IN ({})".format(
                ", ".join(
                    f"('{each}', '{shape}')" for each, shapes in SHAPES.items() for shape in shapes
                )
            ),
            name="ck_item_types_shape",
        ),
    )
    isolate("item_types")
    stamp("item_types")
    referable("item_types")
    op.create_unique_constraint(
        "uq_item_types_kind_name", "item_types", ["organization_id", "kind", "name"]
    )
    op.create_index(
        "uq_item_types_default",
        "item_types",
        ["organization_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column("categories", sa.Column("default_type_id", sa.BigInteger(), nullable=True))
    _set_null_reference("categories", "default_type_id", "item_types")

    for table in TYPED:
        op.add_column(table, sa.Column("type_id", sa.BigInteger(), nullable=True))
    op.add_column("layouts", sa.Column("type_id", sa.BigInteger(), nullable=True))

    defaults = ", ".join(
        f"('{each}'::category_kind, '{shapes[0]}')" for each, shapes in SHAPES.items()
    )
    _lift(
        "INSERT INTO item_types (organization_id, kind, shape, is_default) "
        f"SELECT o.id, d.kind, d.shape, true FROM organizations o CROSS JOIN (VALUES {defaults}) "
        "AS d (kind, shape)",
        *(
            f"UPDATE {table} r SET type_id = t.id FROM item_types t "
            f"WHERE t.organization_id = r.organization_id AND t.kind = '{each}' AND t.is_default"
            for table, each in zip(TYPED, ("material", "base", "product"), strict=True)
        ),
        "UPDATE layouts l SET type_id = t.id FROM item_types t "
        "WHERE t.organization_id = l.organization_id AND t.kind::text = l.kind::text "
        "AND t.is_default AND l.view = 'detail'",
    )
    for table in TYPED:
        op.alter_column(table, "type_id", nullable=False)
        reference(table, "type_id", "item_types")

    reference("layouts", "type_id", "item_types", ondelete="CASCADE")
    op.drop_constraint("uq_layouts_view", "layouts")
    op.create_index(
        "uq_layouts_view",
        "layouts",
        ["organization_id", "kind", "view", "type_id"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        "ck_layouts_typed",
        "layouts",
        "(view = 'detail' AND kind IN ('base', 'material', 'product')) = (type_id IS NOT NULL)",
    )

    op.add_column("products", sa.Column("ingredient_name", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("allergens", postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column("products", sa.Column("fragrance_allergens", postgresql.JSONB(), nullable=True))
    op.create_check_constraint(
        "ck_products_allergens",
        "products",
        "allergens <@ ARRAY[{}]::text[]".format(", ".join(f"'{each}'" for each in ALLERGENS)),
    )
    op.create_check_constraint(
        "ck_products_fragrance_allergens",
        "products",
        "jsonb_typeof(fragrance_allergens) = 'object'",
    )


def downgrade() -> None:
    for kind in ("material", "base", "product"):
        search.drop_source(kind)
    op.drop_constraint("ck_products_fragrance_allergens", "products")
    op.drop_constraint("ck_products_allergens", "products")
    for column in ("fragrance_allergens", "allergens", "ingredient_name"):
        op.drop_column("products", column)

    # Each kind keeps its default type's page, and loses its other types'.
    _lift("DELETE FROM layouts l USING item_types t WHERE t.id = l.type_id AND NOT t.is_default")
    op.drop_constraint("ck_layouts_typed", "layouts")
    op.drop_index("uq_layouts_view", "layouts")
    op.create_unique_constraint("uq_layouts_view", "layouts", ["organization_id", "kind", "view"])
    op.drop_constraint("fk_layouts_type_id", "layouts")
    op.drop_column("layouts", "type_id")
    for table in TYPED:
        op.drop_constraint(f"fk_{table}_type_id", table)
        op.drop_column(table, "type_id")
    op.drop_constraint("fk_categories_default_type_id", "categories")
    op.drop_column("categories", "default_type_id")
    op.drop_table("item_types")
