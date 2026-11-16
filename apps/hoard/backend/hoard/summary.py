"""Item figures: the one read of what is shown of a material, a base or a
product's variation, for as many of them as a page shows at once. Nothing
else works these figures out.

* **Stock** at each location (:func:`stock`): what is on hand; what open
  orders **commit** and have not shipped (``hoard.orders``); what held lots
  hold (``hoard.holds``); and what lots past their expiry date hold, but for
  those on hold. What is **available** is on hand less what is committed and
  on hold, which is what Hoard tells each store it may sell. What is
  **usable** is what is available less what is expired, which is what a run
  plans to use (``hoard.runs``).
* **Could make** at a location: the most batches its recipe's lines cover
  with what is usable of each there (``hoard.units.covered``), times what a
  batch makes. A run draws its lines from one location, so its **potential**
  is the sum of each location's, never the scarcest line's across them all.
  A line that is itself a base counts what is usable of it, not what could be
  made of it. An item with no recipe, or a recipe with no lines, has none.
* **Made**: what its completed runs made, those made at once included. A run
  reopened counts again once it is completed again.
* **Last stocktake** at each location, and the latest of them: the date of a
  stocktake that counted it, not one whose row was left uncounted.
* **Cost** (:func:`costs`): its average cost (``hoard.costing``), or, while
  that is nothing, its **last known** cost, which is its average before its
  stock last ran out: the unit cost on the latest of its adjustments that has
  one, since a movement out records the average at the time and the average
  starts again from nothing only after one. An item that never had a cost has
  none. A recipe is costed from these (:func:`recipe_cost`), with its labour.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import orders, recipes, units
from hoard.holds import held_lots
from hoard.records import (
    PLACES,
    Adjustment,
    Item,
    Location,
    Lot,
    Order,
    OrderLine,
    OrderStatus,
    ProductionRun,
    Recipe,
    RunStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    StockLevel,
    Stocktake,
    StocktakeRow,
)

#: The minutes an hour of labour is.
HOUR = Decimal(60)


@dataclass
class Stock:
    """An item's stock at a location, or at all of them."""

    on_hand: Decimal = Decimal(0)
    committed: Decimal = Decimal(0)
    on_hold: Decimal = Decimal(0)
    #: What its unheld lots past their expiry date hold; nothing unless the
    #: day was given.
    expired: Decimal = Decimal(0)

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.committed - self.on_hold

    @property
    def usable(self) -> Decimal:
        return self.available - self.expired

    def __add__(self, other: Stock) -> Stock:
        return Stock(
            self.on_hand + other.on_hand,
            self.committed + other.committed,
            self.on_hold + other.on_hold,
            self.expired + other.expired,
        )


async def stock(
    db: AsyncSession, item_ids: Iterable[int], today: date | None = None
) -> dict[int, dict[int, Stock]]:
    """Each item's stock at each location it has any at, by item and then by
    location; with ``today``, what is expired too."""
    ids = list(set(item_ids))
    found: dict[int, dict[int, Stock]] = {item_id: defaultdict(Stock) for item_id in ids}
    if not ids:
        return found
    levels = await db.exec(
        select(
            col(StockLevel.item_id), col(StockLevel.location_id), col(StockLevel.quantity)
        ).where(col(StockLevel.item_id).in_(ids))
    )
    for item_id, location_id, quantity in levels:
        found[item_id][location_id].on_hand += quantity
    shipped = (
        select(func.coalesce(func.sum(ShipmentLine.quantity), 0))
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .where(
            ShipmentLine.order_line_id == OrderLine.id,
            Shipment.status == ShipmentStatus.SHIPPED,
        )
        .correlate(OrderLine)
        .scalar_subquery()
    )
    committed = await db.exec(
        select(
            OrderLine.item_id,
            Order.location_id,
            func.sum(func.greatest(orders.SELLING - shipped, 0)),
        )
        .join(Order, col(Order.id) == OrderLine.order_id)
        .where(
            col(OrderLine.item_id).in_(ids),
            Order.status == OrderStatus.OPEN,
            orders.ships_itself(),
        )
        .group_by(col(OrderLine.item_id), col(Order.location_id))
    )
    for item_id, location_id, quantity in committed:
        found[item_id][location_id].committed += Decimal(quantity)
    held = col(Lot.id).in_(held_lots())
    lots = select(Lot.item_id, Lot.location_id, func.sum(func.greatest(Lot.balance, 0))).group_by(
        col(Lot.item_id), col(Lot.location_id)
    )
    for item_id, location_id, quantity in await db.exec(
        lots.where(col(Lot.item_id).in_(ids), held)
    ):
        found[item_id][location_id].on_hold += Decimal(quantity)
    if today is not None:
        for item_id, location_id, quantity in await db.exec(
            lots.where(col(Lot.item_id).in_(ids), ~held, col(Lot.expires_on) < today)
        ):
            found[item_id][location_id].expired += Decimal(quantity)
    return found


async def stock_at(
    db: AsyncSession, item_id: int, location_id: int | None = None, today: date | None = None
) -> Stock:
    """An item's stock at one location, or at all of them; with ``today``,
    what is expired too."""
    at = (await stock(db, [item_id], today))[item_id]
    if location_id is not None:
        return at.get(location_id, Stock())
    return sum(at.values(), Stock())


@dataclass(frozen=True)
class Cost:
    """What one tracking unit of an item cost."""

    unit_cost: Decimal
    #: Whether it is its last known cost, its average being nothing now.
    last_known: bool


async def costs(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, Cost]:
    """Each item's cost: its average, or its last known cost while that is
    nothing. An item that never had a cost is left out."""
    ids = list(set(item_ids))
    averages = await db.exec(
        select(col(Item.id), col(Item.average_cost)).where(
            col(Item.id).in_(ids), Item.average_cost != 0
        )
    )
    found = {item_id: Cost(average, last_known=False) for item_id, average in averages}
    latest = await db.exec(
        select(col(Adjustment.item_id), col(Adjustment.unit_cost))
        .where(
            col(Adjustment.item_id).in_([item_id for item_id in ids if item_id not in found]),
            Adjustment.unit_cost != 0,
        )
        .ext(distinct_on(col(Adjustment.item_id)))
        .order_by(
            col(Adjustment.item_id), col(Adjustment.dated_on).desc(), col(Adjustment.id).desc()
        )
    )
    for item_id, unit_cost in latest:
        found[item_id] = Cost(unit_cost, last_known=True)
    return found


@dataclass(frozen=True)
class RecipeCost:
    """What a recipe's batches cost."""

    #: Each line's cost: its quantity times its item's cost; None for an item
    #: that never had a cost.
    lines: list[Decimal | None]
    #: Each line's: whether its item's cost is its last known one.
    last_known: list[bool]
    #: Its minutes times the labour rate; None without either.
    labour: Decimal | None
    #: The lines' costs and the labour together.
    total: Decimal
    #: Whether a line has no cost, so the total is less than it should be.
    incomplete: bool
    #: The total over what the batches make.
    unit_cost: Decimal


def recipe_cost(
    item_ids: Sequence[int],
    quantities: Sequence[Decimal],
    known: dict[int, Cost],
    *,
    makes: Decimal,
    batches: Decimal,
    minutes: int | None,
    rate: Decimal | None,
) -> RecipeCost:
    """What ``quantities`` of ``item_ids``, a recipe's lines over ``batches``
    that make ``makes``, cost at each item's cost in ``known``, with
    ``minutes`` of labour a batch at ``rate`` an hour."""
    lines = [
        None if item_id not in known else (quantity * known[item_id].unit_cost).quantize(PLACES)
        for item_id, quantity in zip(item_ids, quantities, strict=True)
    ]
    labour = (
        None
        if minutes is None or rate is None
        else (units.scaled(Decimal(minutes), batches) * rate / HOUR).quantize(PLACES)
    )
    total = sum((cost for cost in lines if cost is not None), labour or Decimal(0))
    return RecipeCost(
        lines=lines,
        last_known=[item_id in known and known[item_id].last_known for item_id in item_ids],
        labour=labour,
        total=total,
        incomplete=None in lines,
        unit_cost=(total / makes).quantize(PLACES),
    )


@dataclass
class AtLocation:
    """An item's figures at one location."""

    stock: Stock = field(default_factory=Stock)
    #: What its recipe could make there; None with no recipe or no lines.
    could_make: Decimal | None = None
    last_stocktake: date | None = None


@dataclass
class Summary:
    """An item's figures: at each location it has stock at, was counted at, or
    could make some at, and in all."""

    locations: dict[int, AtLocation]
    #: What it could make at every location together; None with no recipe or
    #: no lines.
    potential: Decimal | None
    made: Decimal
    last_stocktake: date | None

    @property
    def stock(self) -> Stock:
        return sum((at.stock for at in self.locations.values()), Stock())


async def _could_make(
    db: AsyncSession, item_ids: list[int], today: date
) -> dict[int, dict[int, Decimal]]:
    """What each item with a recipe that has lines could make at every
    location, by item and then by location."""
    found = (
        await db.exec(
            select(Recipe).where(
                or_(col(Recipe.base_id).in_(item_ids), col(Recipe.variation_id).in_(item_ids))
            )
        )
    ).all()
    lines = await recipes.lines_of(db, [recipe.id for recipe in found])
    held = await stock(db, {line.item_id for held in lines.values() for line in held}, today)
    locations = (await db.exec(select(Location.id))).all()
    could: dict[int, dict[int, Decimal]] = {}
    for recipe in found:
        quantities = [line.quantity for line in lines[recipe.id]]
        if not quantities:
            continue
        at = could[recipe.base_id or recipe.variation_id or 0] = {}
        for location_id in locations:
            usable = [
                held[line.item_id].get(location_id, Stock()).usable for line in lines[recipe.id]
            ]
            # The batches a run there plans to make as much as it can (``hoard.runs.plan``).
            covers = units.covered(quantities, usable) or Decimal(0)
            at[location_id] = units.scaled(recipe.batch_quantity, units.most_batches(covers))
    return could


async def summarise(
    db: AsyncSession, item_ids: Iterable[int], today: date | None = None
) -> dict[int, Summary]:
    """Each item's figures, as they stand ``today``."""
    today = today or date.today()
    ids = list(set(item_ids))
    held = await stock(db, ids, today)
    could = await _could_make(db, ids, today)
    made = dict(
        (
            await db.exec(
                select(ProductionRun.item_id, func.sum(ProductionRun.actual_quantity))
                .where(
                    col(ProductionRun.item_id).in_(ids),
                    ProductionRun.status == RunStatus.COMPLETED,
                )
                .group_by(col(ProductionRun.item_id))
            )
        ).all()
    )
    counted = await db.exec(
        select(StocktakeRow.item_id, Stocktake.location_id, func.max(Stocktake.dated_on))
        .join(Stocktake, col(Stocktake.id) == StocktakeRow.stocktake_id)
        .where(col(StocktakeRow.item_id).in_(ids), col(StocktakeRow.counted).is_not(None))
        .group_by(col(StocktakeRow.item_id), col(Stocktake.location_id))
    )
    found: dict[int, Summary] = {}
    for item_id in ids:
        at = {location_id: AtLocation(stock=level) for location_id, level in held[item_id].items()}
        for location_id, quantity in could.get(item_id, {}).items():
            if quantity > 0 or location_id in at:
                at.setdefault(location_id, AtLocation()).could_make = quantity
        found[item_id] = Summary(
            locations=at,
            potential=sum(could[item_id].values(), Decimal(0)) if item_id in could else None,
            made=Decimal(made.get(item_id) or 0),
            last_stocktake=None,
        )
    for item_id, location_id, dated_on in counted:
        summary = found[item_id]
        at = summary.locations.setdefault(location_id, AtLocation())
        at.last_stocktake = dated_on
        if item_id in could and at.could_make is None:
            at.could_make = could[item_id][location_id]
        summary.last_stocktake = max(dated_on, summary.last_stocktake or dated_on)
    return found
