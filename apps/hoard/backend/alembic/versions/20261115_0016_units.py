"""Units that know what they measure, recipes written by ratio or in any unit
that converts, and the weight of one unit for declarations.

A unit whose name is one of a standard unit's names becomes that standard
unit, keeping its standard name; two of a community's units that are the same
standard unit become one, every item naming either naming the one kept. A
material bought and tracked in two standard units of one measure takes their
real conversion. Every recipe line was written by amount, in its item's
tracking unit.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from seneschal_core.migrations import reference

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLACES = Decimal("0.000001")

#: The standard units as this revision knows them: each one's key, the name
#: its row keeps, its measure, its size against the measure's first unit, and
#: the names it is found by.
STANDARD: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("milligram", "mg", "mass", "0.001", ("milligrams", "milligramme", "milligrammes")),
    ("gram", "g", "mass", "1", ("gm", "gms", "grams", "gramme", "grammes")),
    (
        "kilogram",
        "kg",
        "mass",
        "1000",
        ("kgs", "kilo", "kilos", "kilograms", "kilogramme", "kilogrammes"),
    ),
    ("ounce", "oz", "mass", "28.349523125", ("ozs", "ounces")),
    ("pound", "lb", "mass", "453.59237", ("lbs", "pounds")),
    ("ton", "ton", "mass", "907184.74", ("tons", "short ton", "short tons")),
    ("tonne", "tonne", "mass", "1000000", ("tonnes", "metric ton", "metric tons")),
    ("millilitre", "mL", "volume", "1", ("millilitres", "milliliter", "milliliters")),
    ("litre", "L", "volume", "1000", ("ltr", "litres", "liter", "liters")),
    ("teaspoon", "tsp", "volume", "4.92892159375", ("tsps", "teaspoons")),
    ("tablespoon", "tbsp", "volume", "14.78676478125", ("tbsps", "tbs", "tablespoons")),
    ("fluid_ounce", "fl oz", "volume", "29.5735295625", ("floz", "fl oz (us)", "fluid ounces")),
    ("cup", "cup", "volume", "236.5882365", ("cups", "cup (us)", "us cup")),
    ("pint", "pt", "volume", "473.176473", ("pint", "pints", "pint (us)")),
    ("quart", "qt", "volume", "946.352946", ("quarts", "quart (us)")),
    ("gallon", "gal", "volume", "3785.411784", ("gallons", "gallon (us)")),
    ("millimetre", "mm", "length", "1", ("millimetres", "millimeter", "millimeters")),
    ("centimetre", "cm", "length", "10", ("centimetres", "centimeter", "centimeters")),
    ("metre", "m", "length", "1000", ("metres", "meter", "meters")),
    ("inch", "in", "length", "25.4", ("inches",)),
    ("foot", "ft", "length", "304.8", ("feet",)),
    ("yard", "yd", "length", "914.4", ("yds", "yards")),
    ("each", "each", "count", "1", ("ea", "pc", "pcs", "piece", "pieces")),
    ("dozen", "dozen", "count", "12", ("dozens", "doz", "dz")),
)


def _normalized(name: str) -> str:
    return " ".join(name.lower().replace(".", "").replace("_", " ").split())


ALIASES = {
    _normalized(alias): key for key, name, _, _, found in STANDARD for alias in (key, name, *found)
}
NAMES = {key: name for key, name, _, _, _ in STANDARD}
SIZES = {key: (measure, Decimal(size)) for key, _, measure, size, _ in STANDARD}

#: The tables this revision writes rows of, with their row security lifted
#: while it does.
WRITTEN = ("units", "materials", "bases", "recipe_lines")


def _match_units(bind: sa.Connection) -> None:
    """Make each unit named like a standard unit that unit, one row of it per
    organization."""
    kept: dict[tuple[int, str], int] = {}
    for unit_id, organization_id, name in bind.execute(
        sa.text("SELECT id, organization_id, name FROM units ORDER BY id")
    ):
        key = ALIASES.get(_normalized(name))
        if key is None:
            continue
        keep = kept.setdefault((organization_id, key), unit_id)
        if keep == unit_id:
            continue
        for table, column in (
            ("materials", "tracking_unit_id"),
            ("materials", "purchase_unit_id"),
            ("bases", "tracking_unit_id"),
        ):
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :keep WHERE {column} = :merged"),
                {"keep": keep, "merged": unit_id},
            )
        bind.execute(sa.text("DELETE FROM units WHERE id = :merged"), {"merged": unit_id})
    for (_, key), unit_id in kept.items():
        bind.execute(
            sa.text("UPDATE units SET standard = :key, name = :name WHERE id = :id"),
            {"key": key, "name": NAMES[key], "id": unit_id},
        )


def _real_conversions(bind: sa.Connection) -> None:
    """A material bought in its tracking unit converts at one, and one bought
    and tracked in standard units of one measure at their real factor."""
    bind.execute(
        sa.text(
            "UPDATE materials SET purchase_unit_id = NULL, purchase_conversion = 1 "
            "WHERE purchase_unit_id = tracking_unit_id"
        )
    )
    pairs = bind.execute(
        sa.text(
            "SELECT m.id, bought.standard, tracked.standard FROM materials m "
            "JOIN units bought ON bought.id = m.purchase_unit_id "
            "JOIN units tracked ON tracked.id = m.tracking_unit_id "
            "WHERE bought.standard IS NOT NULL AND tracked.standard IS NOT NULL"
        )
    )
    for material_id, bought, tracked in pairs.all():
        (bought_measure, bought_size), (tracked_measure, tracked_size) = (
            SIZES[bought],
            SIZES[tracked],
        )
        conversion = (bought_size / tracked_size).quantize(PLACES)
        if bought_measure == tracked_measure and conversion > 0:
            bind.execute(
                sa.text("UPDATE materials SET purchase_conversion = :conversion WHERE id = :id"),
                {"conversion": conversion, "id": material_id},
            )


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("units", sa.Column("standard", sa.Text(), nullable=True))
    op.add_column("materials", sa.Column("unit_weight", sa.Numeric(20, 6), nullable=True))
    op.create_check_constraint("ck_materials_unit_weight", "materials", "unit_weight > 0")
    op.add_column(
        "recipes", sa.Column("by_ratio", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("recipes", sa.Column("total", sa.Numeric(20, 6), nullable=True))
    op.add_column("recipes", sa.Column("total_unit_id", sa.BigInteger(), nullable=True))
    op.add_column("recipe_lines", sa.Column("written", sa.Numeric(20, 6), nullable=True))
    op.add_column("recipe_lines", sa.Column("unit_id", sa.BigInteger(), nullable=True))

    for table in WRITTEN:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    try:
        _match_units(bind)
        _real_conversions(bind)
        op.execute(
            "UPDATE recipe_lines SET written = quantity, unit_id = coalesce("
            "(SELECT tracking_unit_id FROM materials WHERE materials.id = item_id), "
            "(SELECT tracking_unit_id FROM bases WHERE bases.id = item_id))"
        )
    finally:
        for table in WRITTEN:
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.create_index(
        "uq_units_standard",
        "units",
        ["organization_id", "standard"],
        unique=True,
        postgresql_where=sa.text("standard IS NOT NULL"),
    )
    reference("recipes", "total_unit_id", "units")
    op.create_check_constraint(
        "ck_recipes_ratio",
        "recipes",
        "by_ratio = (total IS NOT NULL) AND by_ratio = (total_unit_id IS NOT NULL) AND total > 0",
    )
    op.alter_column("recipe_lines", "written", nullable=False)
    reference("recipe_lines", "unit_id", "units")
    op.drop_constraint("ck_recipe_lines_quantity", "recipe_lines")
    op.create_check_constraint(
        "ck_recipe_lines_quantity", "recipe_lines", "quantity > 0 AND written > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_recipe_lines_quantity", "recipe_lines")
    op.create_check_constraint("ck_recipe_lines_quantity", "recipe_lines", "quantity > 0")
    op.drop_constraint("fk_recipe_lines_unit_id", "recipe_lines")
    op.drop_column("recipe_lines", "unit_id")
    op.drop_column("recipe_lines", "written")
    op.drop_constraint("ck_recipes_ratio", "recipes")
    op.drop_constraint("fk_recipes_total_unit_id", "recipes")
    op.drop_column("recipes", "total_unit_id")
    op.drop_column("recipes", "total")
    op.drop_column("recipes", "by_ratio")
    op.drop_constraint("ck_materials_unit_weight", "materials")
    op.drop_column("materials", "unit_weight")
    op.drop_index("uq_units_standard", "units")
    op.drop_column("units", "standard")
