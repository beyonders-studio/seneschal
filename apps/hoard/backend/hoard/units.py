"""Units, and every quantity that changes unit or scale.

Hoard ships a list of **standard units** (:data:`STANDARD_UNITS`), each with
what it measures and how big it is against the first unit of its measure: the
gram, the millilitre, the millimetre and one of a thing. Cup, pint, quart,
gallon and fluid ounce are US measures. A community's own units (a jar, a bag)
measure nothing Hoard knows, and convert only to themselves.

Everything that converts or scales goes through this module:

* converting between units (:func:`factor`, :func:`convert`), which a
  purchase's conversion takes too (:func:`purchase_conversion`);
* a recipe line's quantity in its item's tracking unit, from the amount typed
  in its own unit or from its parts of a ratio (:func:`recipe`);
* ratio parts from amounts (:func:`parts`) and amounts relative to the
  smallest (:func:`relative`);
* scaling by batches, by an output quantity (:func:`scale`, :func:`scaled`) or
  by an amount of one ingredient (:func:`use`);
* shares by weight (:func:`percents`), and the weight a declaration counts
  (:func:`grams`).

Quantities are kept to six places (``hoard.records.PLACES``). Each step works
on exact decimals and rounds only what it answers, so a line keeps what was
typed and its quantity is always derived from that.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from functools import reduce
from math import gcd

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import PLACES, Base, Material, Unit
from seneschal_core.db import WriteSession

#: A line's share of its recipe, as a percentage to two places.
PERCENT_PLACES = Decimal("0.01")


class Measure(StrEnum):
    """What a standard unit measures."""

    MASS = "mass"
    VOLUME = "volume"
    LENGTH = "length"
    COUNT = "count"


class UnitError(StrEnum):
    """Why a quantity was refused: codes pages translate."""

    #: It converts to nothing Hoard keeps: less than the smallest amount, six
    #: places down, of the unit it is counted in.
    AMOUNT_TOO_SMALL = "AMOUNT_TOO_SMALL"
    #: A ratio's ingredients total adds up the lines measured like it, and none is.
    RECIPE_RATIO_TOTAL = "RECIPE_RATIO_TOTAL"
    #: An amount is in a unit that doesn't convert to the one it is counted in.
    UNIT_NOT_CONVERTIBLE = "UNIT_NOT_CONVERTIBLE"


class UnitRefused(Exception):
    def __init__(self, code: UnitError) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class StandardUnit:
    #: Its key, which pages translate its name by.
    key: str
    #: The name a community's row of it keeps, which exports and events show.
    name: str
    measure: Measure
    #: How many of its measure's first unit it is.
    size: Decimal
    #: The names it is found by, besides its key and its name.
    aliases: tuple[str, ...]


def _unit(key: str, name: str, measure: Measure, size: str, *aliases: str) -> StandardUnit:
    return StandardUnit(key, name, measure, Decimal(size), aliases)


#: Hoard's standard units, in the order they are listed. "oz" is the mass
#: ounce, and only "fl oz" the fluid one; "ton" is the US short ton and
#: "tonne" the metric one; "t" and "T" match nothing, being a teaspoon, a
#: tablespoon or a tonne depending on whose kitchen it is.
STANDARD_UNITS: tuple[StandardUnit, ...] = (
    _unit("milligram", "mg", Measure.MASS, "0.001", "milligrams", "milligramme", "milligrammes"),
    _unit("gram", "g", Measure.MASS, "1", "gm", "gms", "grams", "gramme", "grammes"),
    _unit(
        "kilogram",
        "kg",
        Measure.MASS,
        "1000",
        "kgs",
        "kilo",
        "kilos",
        "kilograms",
        "kilogramme",
        "kilogrammes",
    ),
    _unit("ounce", "oz", Measure.MASS, "28.349523125", "ozs", "ounces"),
    _unit("pound", "lb", Measure.MASS, "453.59237", "lbs", "pounds"),
    _unit("ton", "ton", Measure.MASS, "907184.74", "tons", "short ton", "short tons"),
    _unit("tonne", "tonne", Measure.MASS, "1000000", "tonnes", "metric ton", "metric tons"),
    _unit(
        "millilitre",
        "mL",
        Measure.VOLUME,
        "1",
        "millilitres",
        "milliliter",
        "milliliters",
    ),
    _unit("litre", "L", Measure.VOLUME, "1000", "ltr", "litres", "liter", "liters"),
    _unit("teaspoon", "tsp", Measure.VOLUME, "4.92892159375", "tsps", "teaspoons"),
    _unit("tablespoon", "tbsp", Measure.VOLUME, "14.78676478125", "tbsps", "tbs", "tablespoons"),
    _unit(
        "fluid_ounce",
        "fl oz",
        Measure.VOLUME,
        "29.5735295625",
        "floz",
        "fl oz (us)",
        "fluid ounces",
    ),
    _unit("cup", "cup", Measure.VOLUME, "236.5882365", "cups", "cup (us)", "us cup"),
    _unit("pint", "pt", Measure.VOLUME, "473.176473", "pint", "pints", "pint (us)"),
    _unit("quart", "qt", Measure.VOLUME, "946.352946", "quarts", "quart (us)"),
    _unit("gallon", "gal", Measure.VOLUME, "3785.411784", "gallons", "gallon (us)"),
    _unit("millimetre", "mm", Measure.LENGTH, "1", "millimetres", "millimeter", "millimeters"),
    _unit("centimetre", "cm", Measure.LENGTH, "10", "centimetres", "centimeter", "centimeters"),
    _unit("metre", "m", Measure.LENGTH, "1000", "metres", "meter", "meters"),
    _unit("inch", "in", Measure.LENGTH, "25.4", "inches"),
    _unit("foot", "ft", Measure.LENGTH, "304.8", "feet"),
    _unit("yard", "yd", Measure.LENGTH, "914.4", "yds", "yards"),
    _unit("each", "each", Measure.COUNT, "1", "ea", "pc", "pcs", "piece", "pieces"),
    _unit("dozen", "dozen", Measure.COUNT, "12", "dozens", "doz", "dz"),
)
STANDARD: dict[str, StandardUnit] = {unit.key: unit for unit in STANDARD_UNITS}


def normalized(name: str) -> str:
    """``name`` as aliases are compared: in lower case, without full stops,
    an underscore read as a space, and single spaces."""
    return " ".join(name.lower().replace(".", "").replace("_", " ").split())


def _found_by(unit: StandardUnit) -> tuple[str, ...]:
    return (unit.key.replace("_", " "), unit.name, *unit.aliases)


_ALIASES: dict[str, str] = {
    normalized(alias): unit.key for unit in STANDARD_UNITS for alias in _found_by(unit)
}


def aliases(key: str) -> list[str]:
    """Every name a standard unit is found by, as typed."""
    return list(_found_by(STANDARD[key]))


def match(name: str) -> str | None:
    """The standard unit ``name`` is one of the names of, if any."""
    return _ALIASES.get(normalized(name))


def measure(unit: Unit | None) -> Measure | None:
    """What a unit measures: nothing Hoard knows for a unit of the community's own."""
    return STANDARD[unit.standard].measure if unit is not None and unit.standard else None


async def load(db: AsyncSession) -> dict[int, Unit]:
    """The organization's units, by id."""
    return {unit.id: unit for unit in await db.exec(select(Unit))}


async def add_standard(db: WriteSession) -> None:
    """Give the organization each standard unit it lacks, keeping those it has."""
    await db.execute(
        insert(Unit)
        .values(
            [
                {"organization_id": db.organization_id, "name": unit.name, "standard": unit.key}
                for unit in STANDARD_UNITS
            ]
        )
        .on_conflict_do_nothing()
    )


async def tracking_units(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, int]:
    """The unit each material or base among ``item_ids`` is counted in. A
    variation counts in units of itself, and has none."""
    ids = list(item_ids)
    counted: dict[int, int] = {}
    for kind in (Material, Base):
        found = await db.exec(select(kind.id, kind.tracking_unit_id).where(col(kind.id).in_(ids)))
        counted |= dict(found.all())
    return counted


async def in_tracking_unit(
    db: AsyncSession, item_id: int, amount: Decimal, unit_id: int | None
) -> Decimal:
    """``amount`` of an item in ``unit_id`` as its tracking unit counts it,
    kept to six places: as it is when no unit is named."""
    if unit_id is None:
        return kept(amount)
    known = await load(db)
    tracking = (await tracking_units(db, [item_id])).get(item_id)
    return kept(convert(amount, known.get(unit_id), known.get(tracking or 0)))


def factor(source: Unit | None, target: Unit | None) -> Decimal | None:
    """How many ``target`` units one ``source`` unit is: one for the same
    unit, the standard factor between two standard units of one measure, and
    None when they don't convert. A variation counts in units of itself, with
    no unit, and converts only to that."""
    if source is None or target is None:
        return Decimal(1) if source is target else None
    if source.id == target.id:
        return Decimal(1)
    if not source.standard or not target.standard:
        return None
    left, right = STANDARD[source.standard], STANDARD[target.standard]
    return left.size / right.size if left.measure is right.measure else None


def converts(source: Unit | None, target: Unit | None) -> bool:
    return factor(source, target) is not None


def convert(amount: Decimal, source: Unit | None, target: Unit | None) -> Decimal:
    """``amount`` of ``source`` in ``target`` units, exactly; refused for units
    that don't convert."""
    found = factor(source, target)
    if found is None:
        raise UnitRefused(UnitError.UNIT_NOT_CONVERTIBLE)
    return amount * found


def kept(amount: Decimal) -> Decimal:
    """``amount`` to the places Hoard keeps, refused when that leaves nothing."""
    rounded = amount.quantize(PLACES)
    if rounded <= 0 < amount:
        raise UnitRefused(UnitError.AMOUNT_TOO_SMALL)
    return rounded


def purchase_conversion(purchase: Unit | None, tracking: Unit | None) -> Decimal | None:
    """Tracking units in one purchase unit, when both are standard units of one
    measure; None when the conversion is a number typed in."""
    if purchase is None or tracking is None or purchase.id == tracking.id:
        return None
    found = factor(purchase, tracking)
    return None if found is None else kept(found)


def grams(
    quantity: Decimal, unit: Unit | None, unit_weight: Decimal | None = None
) -> Decimal | None:
    """What ``quantity`` of ``unit`` weighs in grams: by its standard factor
    for a unit of mass, otherwise by ``unit_weight``, the grams one unit
    weighs, when it is known; None when it can't be weighed."""
    if measure(unit) is Measure.MASS:
        assert unit is not None and unit.standard
        return quantity * STANDARD[unit.standard].size
    return None if unit_weight is None else quantity * unit_weight


def percents(weights: Sequence[Decimal | None]) -> list[Decimal | None]:
    """Each line's share of a recipe by weight, as a percentage, from its
    weight in grams (:func:`grams`); a line that can't be weighed has none."""
    total = sum((weight for weight in weights if weight is not None), Decimal(0))
    return [
        None if weight is None or total <= 0 else (weight * 100 / total).quantize(PERCENT_PLACES)
        for weight in weights
    ]


@dataclass(frozen=True)
class RecipeLine:
    """A line as written: the number typed (its amount, or its parts of a
    ratio), the unit it is in, and its item's tracking unit."""

    written: Decimal
    unit: Unit | None
    tracking: Unit | None


@dataclass(frozen=True)
class Figures:
    """A recipe's lines worked out for one batch: each one's amount in its own
    unit (exact), its quantity in its item's tracking unit (kept to six
    places), and its ratio: the parts as typed, or the amounts relative to the
    smallest. ``measures`` holds each line's number in its tracking unit
    before the multiplier, which scaling to one line's amount divides by."""

    amounts: list[Decimal]
    quantities: list[Decimal]
    ratios: list[Decimal]
    measures: list[Decimal]
    #: What one batch's lines are their written numbers times.
    multiplier: Decimal


def measured(lines: Sequence[RecipeLine], unit: Unit | None) -> Decimal | None:
    """What the lines measured like ``unit`` add up to in it, exactly; None
    when none is."""
    counted = [
        convert(line.written, line.unit, unit) for line in lines if converts(line.unit, unit)
    ]
    return sum(counted, Decimal(0)) if counted and unit is not None else None


def recipe(
    lines: Sequence[RecipeLine],
    *,
    total: Decimal | None = None,
    total_unit: Unit | None = None,
) -> Figures:
    """A recipe's lines for one batch, scaled by one multiplier. Written by
    amount (no ``total``), each line's number is its amount. Written by
    ratio, each line's number is its parts, and ``total`` of ``total_unit``
    sets the multiplier: it is split among the lines measured like it, and the
    rest scale along. Each line's quantity converts its amount to its item's
    tracking unit."""
    over, under = Decimal(1), Decimal(1)
    if total is not None:
        counted = measured(lines, total_unit)
        if counted is None:
            raise UnitRefused(UnitError.RECIPE_RATIO_TOTAL)
        over, under = total, counted
    measures = [convert(line.written, line.unit, line.tracking) for line in lines]
    written = [line.written for line in lines]
    return Figures(
        amounts=[number * over / under for number in written],
        quantities=[kept(measure * over / under) for measure in measures],
        ratios=written if total is not None else relative(written),
        measures=measures,
        multiplier=over / under,
    )


def relative(amounts: Sequence[Decimal]) -> list[Decimal]:
    """Each amount relative to the smallest, which reads one."""
    if not amounts:
        return []
    smallest = min(amounts)
    return [(amount / smallest).quantize(PLACES) for amount in amounts]


def parts(amounts: Sequence[Decimal]) -> list[Decimal]:
    """Amounts as parts of a ratio: divided by their common factor when they
    have one above one (454 : 227 : 227 is 2 : 1 : 1), and otherwise as they
    are. Nothing is rounded to make a ratio look tidier."""
    if not amounts:
        return []
    shift = 1 / PLACES
    common = Decimal(reduce(gcd, (int(amount * shift) for amount in amounts))) / shift
    if common <= 1:
        return [amount.quantize(PLACES) for amount in amounts]
    return [(amount / common).quantize(PLACES) for amount in amounts]


def scale(batch_quantity: Decimal, quantity: Decimal | None) -> Decimal:
    """How many batches make ``quantity``: one, when no quantity is asked for."""
    return Decimal(1) if quantity is None else quantity / batch_quantity


def scaled(quantity: Decimal, batches: Decimal, *, down: bool = False) -> Decimal:
    """``quantity`` of a line, ``batches`` over, kept to six places: rounded
    down when ``down``, so it never asks for more than there is."""
    return (quantity * batches).quantize(PLACES, rounding=ROUND_DOWN if down else None)


@dataclass(frozen=True)
class Use:
    """A recipe scaled to use exactly an amount of one of its lines."""

    #: The batches that takes, exactly.
    batches: Decimal
    #: Each line's quantity: the named line's exactly what was asked, the
    #: others rounded down, so rounding never asks for more than there is.
    quantities: list[Decimal]


def use(figures: Figures, index: int, amount: Decimal) -> Use:
    """Scale a recipe worked out as ``figures`` so that line ``index`` takes
    ``amount``, in its tracking unit. Each line is scaled from what was
    written, so a ratio's lines keep its proportions exactly."""
    named = figures.measures[index]
    return Use(
        batches=amount / (named * figures.multiplier),
        quantities=[
            amount.quantize(PLACES)
            if position == index
            else (amount * measure / named).quantize(PLACES, rounding=ROUND_DOWN)
            for position, measure in enumerate(figures.measures)
        ],
    )
