"""Item figures: the one read of what is shown of a material, a base or a
product's variation, for as many of them as a page shows at once. Nothing
else works these figures out.

* **Stock** at each location (:func:`stock`): what is on hand; what open
  orders **commit** and have not shipped (``hoard.orders``); what held lots
  hold (``hoard.holds``); and what lots past their expiry date hold, but for
  those on hold. What is **consigned** is what sits at a consignee's
  location (:attr:`hoard.records.Location.consignee`) for it to sell, less
  what the consignee's own orders there commit and its lots on hold hold.
  What is **available** is on hand less what is committed, on hold and
  consigned, which is what Hoard tells each store it may sell. What is
  **usable** is what is available less what is expired, which is what a run
  plans to use (``hoard.runs``); a consignee's stock is never usable.
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
* **On order** (:func:`on_order`): what its purchase lines not yet received
  will bring, in its tracking unit, so only the part of a split line still to
  come; and whether any purchase they are on is overdue
  (``hoard.purchases.overdue``).
* **Usage** over each :class:`UsageWindow` ending today, and in all: what was
  **received**, by its purchase adjustments, and **used**, by what production
  runs consumed, each on the day it happened. Starting stock, losses,
  corrections, stocktakes, shipments, returns and what a run made count as
  neither.
* **Used in**: each recipe holding it that makes a base or a variation, with
  how much one batch takes in the unit it was written in. A bundle holds
  variations but makes nothing, so it is left out.
* **Cost** (:func:`costs`): its average cost (``hoard.costing``), or, while
  that is nothing, its **last known** cost, which is its average before its
  stock last ran out: the unit cost on the latest of its adjustments that has
  one, since a movement out records the average at the time and the average
  starts again from nothing only after one. An item that never had a cost has
  none. A recipe is costed from these (:func:`recipe_cost`), with its labour.
* **A bundle's variation** holds no stock of its own: what is **available**
  of it is its potential, how many its contents make together from what is
  usable of them, and what is **committed** is how many bundles open orders
  still need. Its contents' own figures count what the bundles need of them.
* **Sales** (:func:`sales`), from its order lines, a bundle's parts included:
  what was **sold**, less what was refunded before it shipped, and what
  **shipped**, less what came back. Of the lines that charge for it, which
  a bundle's parts don't, since their bundle's line does, its **revenue**:
  what shipped and was not refunded or returned, at the line's price less
  its share of its order's discount, without shipping or tax; and its
  **cost**: what each shipment line went out at, less what came back at, a
  bundle's being its parts'. An order counts once it ships. Shipment lines
  that went out with no cost are counted, and only orders in the
  organization's currency count toward revenue and cost: those in another
  are counted apart. Each line counts in its order's group: **wholesale**
  for a store's order a company bought (``hoard.records.Order``),
  **retail** for every other.
* **Pricing** (:func:`pricing`), of a variation: its cost, of its average or
  last known cost (its contents' for a bundle), the labour a unit of its
  recipe takes at the labour rate, and the organization's overhead on both;
  its **price**, its listing's price in a synced store, else its own or its
  product's default price; and the margin left at that price. An item that
  never had a cost has no margin.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import and_, func, or_
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import orders, purchases, recipes, units
from hoard.holds import held_lots
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    Item,
    Listing,
    Location,
    Lot,
    Order,
    OrderLine,
    OrderStatus,
    OrganizationSettings,
    Product,
    ProductionRun,
    Purchase,
    PurchaseLine,
    Recipe,
    RecipeLine,
    ReturnLine,
    RunStatus,
    Shipment,
    ShipmentLine,
    ShipmentStatus,
    StockLevel,
    Stocktake,
    StocktakeRow,
    Store,
    Variation,
)

#: The minutes an hour of labour is.
HOUR = Decimal(60)
#: A percentage's whole.
HUNDRED = Decimal(100)


@dataclass
class Stock:
    """An item's stock at a location, or at all of them."""

    on_hand: Decimal = Decimal(0)
    committed: Decimal = Decimal(0)
    on_hold: Decimal = Decimal(0)
    #: What its unheld lots past their expiry date hold; nothing unless the
    #: day was given, and nothing at a consignee's location.
    expired: Decimal = Decimal(0)
    #: What is at a consignee's location for it to sell.
    consigned: Decimal = Decimal(0)

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.committed - self.on_hold - self.consigned

    @property
    def usable(self) -> Decimal:
        return self.available - self.expired

    def __add__(self, other: Stock) -> Stock:
        return Stock(
            self.on_hand + other.on_hand,
            self.committed + other.committed,
            self.on_hold + other.on_hold,
            self.expired + other.expired,
            self.consigned + other.consigned,
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
    consignees = set((await db.exec(select(Location.id).where(col(Location.consignee)))).all())
    if today is not None:
        for item_id, location_id, quantity in await db.exec(
            lots.where(
                col(Lot.item_id).in_(ids),
                ~held,
                col(Lot.expires_on) < today,
                col(Lot.location_id).not_in(consignees),
            )
        ):
            found[item_id][location_id].expired += Decimal(quantity)
    _consign(found, consignees)
    return found


def _consign(found: dict[int, dict[int, Stock]], consignees: set[int]) -> None:
    """Count what each consignee's location holds for it to sell as consigned."""
    for locations in found.values():
        for location_id, at in locations.items():
            if location_id in consignees:
                at.consigned = max(at.on_hand - at.committed - at.on_hold, Decimal(0))


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


async def _settings(db: AsyncSession) -> OrganizationSettings:
    return (await db.exec(select(OrganizationSettings))).first() or OrganizationSettings()


def share(part: Decimal, whole: Decimal) -> Decimal | None:
    """``part`` as a percentage of ``whole``, to two places; None of nothing."""
    return None if whole == 0 else (part * HUNDRED / whole).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class CostParts:
    """What one of a variation costs, by its parts."""

    #: Its average or last known cost; for a bundle, its contents'.
    goods: Decimal
    #: Whether that is, or holds, a last known cost.
    last_known: bool
    #: What a unit of its recipe's labour costs; None without minutes or a rate.
    labour: Decimal | None
    #: The overhead on the goods and the labour; None until it is set.
    overhead: Decimal | None

    @property
    def total(self) -> Decimal:
        return self.goods + (self.labour or Decimal(0)) + (self.overhead or Decimal(0))


@dataclass(frozen=True)
class Pricing:
    """A variation's cost, its price and the margin left at that price."""

    cost: CostParts | None
    #: Its listing's price in a synced store, else its own or its product's
    #: default price; None without any.
    price: Decimal | None
    #: Whether the price is a store's.
    store_price: bool

    @property
    def margin(self) -> Decimal | None:
        """The price less the cost; None without either."""
        if self.cost is None or self.price is None:
            return None
        return self.price - self.cost.total

    @property
    def margin_share(self) -> Decimal | None:
        """The margin as a percentage of the price."""
        if self.margin is None or self.price is None:
            return None
        return share(self.margin, self.price)


async def store_prices(db: AsyncSession, variation_ids: Iterable[int]) -> dict[int, Decimal]:
    """The price a synced store's listing holds for each variation it lists
    with one, which shows in place of the variation's own."""
    found = (
        await db.exec(
            select(col(Listing.item_id), col(Listing.price))
            .join(Store, col(Store.id) == Listing.store_id)
            .where(
                col(Listing.item_id).in_(list(variation_ids)),
                col(Listing.price).is_not(None),
                col(Store.imported_at).is_not(None),
            )
            .ext(distinct_on(col(Listing.item_id)))
            .order_by(col(Listing.item_id), col(Listing.id))
        )
    ).all()
    return {item_id: price for item_id, price in found if item_id is not None and price is not None}


async def pricing(db: AsyncSession, variation_ids: Iterable[int]) -> dict[int, Pricing]:
    """Each variation's cost, its price and its margin."""
    ids = list(set(variation_ids))
    settings = await _settings(db)
    rows = (
        await db.exec(
            select(Variation, Product)
            .join(Product, col(Product.id) == Variation.product_id)
            .where(col(Variation.id).in_(ids))
        )
    ).all()
    products = {variation.id: product for variation, product in rows}
    own = {
        variation.id: variation.price if variation.price is not None else product.default_price
        for variation, product in rows
    }
    listed = await store_prices(db, ids)
    held = (
        await db.exec(
            select(Recipe).where(
                or_(
                    col(Recipe.variation_id).in_(ids),
                    col(Recipe.product_id).in_(
                        [product.id for product in products.values() if product.bundle]
                    ),
                )
            )
        )
    ).all()
    made = {recipe.variation_id: recipe for recipe in held if recipe.variation_id}
    bundled = {recipe.product_id: recipe for recipe in held if recipe.product_id}
    lines = await recipes.lines_of(db, [recipe.id for recipe in bundled.values()])
    known = await costs(db, [*ids, *(line.item_id for held in lines.values() for line in held)])
    rate, overhead = settings.labour_rate, settings.overhead
    found: dict[int, Pricing] = {}
    for variation_id in ids:
        product = products[variation_id]
        goods: tuple[Decimal, bool] | None = None
        labour = None
        if product.bundle:
            recipe = bundled.get(product.id)
            contents = [] if recipe is None else lines[recipe.id]
            costed = recipe_cost(
                [line.item_id for line in contents],
                [line.quantity for line in contents],
                known,
                makes=recipe.batch_quantity if recipe else Decimal(1),
                batches=Decimal(1),
                minutes=None,
                rate=None,
            )
            if contents and not costed.incomplete:
                goods = costed.unit_cost, any(costed.last_known)
        else:
            if variation_id in known:
                goods = known[variation_id].unit_cost, known[variation_id].last_known
            recipe = made.get(variation_id)
            if recipe is not None and recipe.minutes is not None and rate is not None:
                labour = recipe_cost(
                    [],
                    [],
                    known,
                    makes=recipe.batch_quantity,
                    batches=Decimal(1),
                    minutes=recipe.minutes,
                    rate=rate,
                ).unit_cost
        cost = None
        if goods is not None:
            base = goods[0] + (labour or Decimal(0))
            cost = CostParts(
                goods=goods[0],
                last_known=goods[1],
                labour=labour,
                overhead=None if overhead is None else (base * overhead / HUNDRED).quantize(PLACES),
            )
        found[variation_id] = Pricing(
            cost=cost,
            price=listed.get(variation_id, own[variation_id]),
            store_price=variation_id in listed,
        )
    return found


@dataclass(frozen=True)
class OnOrder:
    """What an item's purchase lines not yet received will bring."""

    #: In its tracking unit.
    quantity: Decimal = Decimal(0)
    #: Whether a purchase bringing some of it is overdue.
    overdue: bool = False


async def on_order(
    db: AsyncSession, item_ids: Iterable[int], today: date | None = None
) -> dict[int, OnOrder]:
    """What each of ``item_ids`` has on order, as it stands ``today``; an item
    with nothing on order is left out."""
    rows = await db.exec(
        select(col(PurchaseLine.item_id), col(PurchaseLine.tracking_quantity), Purchase)
        .join(Purchase, col(Purchase.id) == col(PurchaseLine.purchase_id))
        .where(
            col(PurchaseLine.item_id).in_(list(item_ids)), col(PurchaseLine.received_on).is_(None)
        )
    )
    found: dict[int, OnOrder] = {}
    for item_id, quantity, purchase in rows:
        held = found.get(item_id, OnOrder())
        found[item_id] = OnOrder(
            held.quantity + quantity,
            held.overdue or purchases.overdue(purchase, on_order=True, today=today),
        )
    return found


class UsageWindow(StrEnum):
    """A span of days ending today that usage is read over: the last week,
    the last 1, 3, 6 and 12 months, or all time."""

    WEEK = "week"
    MONTH = "month"
    MONTHS_3 = "months_3"
    MONTHS_6 = "months_6"
    MONTHS_12 = "months_12"
    ALL = "all"


#: The calendar months each window counted in months spans.
_MONTHS = {
    UsageWindow.MONTH: 1,
    UsageWindow.MONTHS_3: 3,
    UsageWindow.MONTHS_6: 6,
    UsageWindow.MONTHS_12: 12,
}


def window_start(window: UsageWindow, today: date) -> date | None:
    """The first day of ``window`` ending ``today``: the week is today and
    the six days before it, and a span of months starts the day after the same
    day that many months before, or after the month's last day when it has no
    such day. None for all time."""
    if window is UsageWindow.ALL:
        return None
    if window is UsageWindow.WEEK:
        return today - timedelta(days=6)
    index = today.year * 12 + today.month - 1 - _MONTHS[window]
    year, month = index // 12, index % 12 + 1
    before = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
    return before + timedelta(days=1)


@dataclass(frozen=True)
class Usage:
    """What an item received and what runs used of it over one window."""

    window: UsageWindow
    received: Decimal
    used: Decimal


async def _usage(db: AsyncSession, item_ids: list[int], today: date) -> dict[int, list[Usage]]:
    """Each item's usage over every window, in :class:`UsageWindow` order."""
    received = col(Adjustment.kind) == AdjustmentKind.PURCHASE
    used = and_(
        col(Adjustment.kind) == AdjustmentKind.PRODUCTION,
        col(Adjustment.production_run_line_id).is_not(None),
    )
    columns = []
    for window in UsageWindow:
        start = window_start(window, today)
        within = [] if start is None else [col(Adjustment.dated_on) >= start]
        columns += [
            func.coalesce(func.sum(Adjustment.change).filter(received, *within), 0),
            func.coalesce(func.sum(-col(Adjustment.change)).filter(used, *within), 0),
        ]
    rows = await db.execute(
        sa_select(col(Adjustment.item_id), *columns)
        .where(
            col(Adjustment.item_id).in_(item_ids),
            col(Adjustment.dated_on) <= today,
            or_(received, used),
        )
        .group_by(col(Adjustment.item_id))
    )
    found = {
        item_id: [Usage(window, Decimal(0), Decimal(0)) for window in UsageWindow]
        for item_id in item_ids
    }
    for item_id, *sums in rows.all():
        found[item_id] = [
            Usage(window, Decimal(sums[2 * at]), Decimal(sums[2 * at + 1]))
            for at, window in enumerate(UsageWindow)
        ]
    return found


@dataclass(frozen=True)
class UsedIn:
    """A recipe that holds an item and makes a base or a variation."""

    recipe_id: int
    #: The base or the variation it makes.
    makes: int
    #: What a batch makes, in the base's tracking unit or of the variation.
    batch_quantity: Decimal
    #: How much one batch takes of the item, in ``unit_id``: the unit the line
    #: was written in, or its item's tracking unit; none for a variation,
    #: which counts in units of itself.
    amount: Decimal
    unit_id: int | None


async def _used_in(db: AsyncSession, item_ids: list[int]) -> dict[int, list[UsedIn]]:
    """The recipes holding each item, but bundles', oldest first."""
    holding = (
        await db.exec(
            select(Recipe)
            .where(
                col(Recipe.id).in_(
                    select(RecipeLine.recipe_id).where(col(RecipeLine.item_id).in_(item_ids))
                ),
                col(Recipe.product_id).is_(None),
            )
            .order_by(col(Recipe.id))
        )
    ).all()
    lines = await recipes.lines_of(db, [recipe.id for recipe in holding])
    known = await recipes.know(db, {line.item_id for held in lines.values() for line in held})
    found: dict[int, list[UsedIn]] = {item_id: [] for item_id in item_ids}
    for recipe in holding:
        held = lines[recipe.id]
        worked = await recipes.work_out(
            db,
            recipe,
            [recipes.Line(line.item_id, line.written, line.unit_id) for line in held],
            known,
        )
        for line, amount, unit in zip(held, worked.figures.amounts, worked.units, strict=True):
            if line.item_id in found:
                found[line.item_id].append(
                    UsedIn(
                        recipe_id=recipe.id,
                        makes=recipe.base_id or recipe.variation_id or 0,
                        batch_quantity=recipe.batch_quantity,
                        amount=units.scaled(amount, Decimal(1)),
                        unit_id=None if unit is None else unit.id,
                    )
                )
    return found


@dataclass
class Sales:
    """What an item sold and shipped, and what the lines charging for it
    earned and what what they shipped cost."""

    sold: Decimal = Decimal(0)
    shipped: Decimal = Decimal(0)
    #: How many bundles open orders still need, for a bundle's variation.
    bundles_committed: Decimal = Decimal(0)
    revenue: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    #: The shipment lines that went out with no cost.
    uncosted: int = 0
    #: The orders in another currency than the organization's, left out of
    #: revenue and cost.
    left_out: frozenset[int] = frozenset()

    @property
    def profit(self) -> Decimal:
        return self.revenue - self.cost

    @property
    def profit_share(self) -> Decimal | None:
        """The profit as a percentage of the revenue."""
        return share(self.profit, self.revenue)

    def __add__(self, other: Sales) -> Sales:
        return Sales(
            self.sold + other.sold,
            self.shipped + other.shipped,
            self.bundles_committed + other.bundles_committed,
            self.revenue + other.revenue,
            self.cost + other.cost,
            self.uncosted + other.uncosted,
            self.left_out | other.left_out,
        )


@dataclass
class SalesGroups:
    """An item's sales in each group: to retail and to wholesale customers."""

    retail: Sales = field(default_factory=Sales)
    wholesale: Sales = field(default_factory=Sales)

    @property
    def whole(self) -> Sales:
        return self.retail + self.wholesale

    def __add__(self, other: SalesGroups) -> SalesGroups:
        return SalesGroups(self.retail + other.retail, self.wholesale + other.wholesale)


@dataclass
class _Sent:
    """What an order line's shipped shipments sent of it, what came back, and
    what both cost."""

    shipped: Decimal = Decimal(0)
    returned: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)
    uncosted: int = 0


async def sales(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, SalesGroups]:
    """Each item's sales in each group, in the organization's currency."""
    ids = list(set(item_ids))
    found = {item_id: SalesGroups() for item_id in ids}
    if not ids:
        return found
    currency = (await _settings(db)).currency
    own = (
        await db.exec(
            select(OrderLine, Order)
            .join(Order, col(Order.id) == OrderLine.order_id)
            .where(col(OrderLine.item_id).in_(ids))
        )
    ).all()
    parts: dict[int, list[OrderLine]] = defaultdict(list)
    for part in await db.exec(
        select(OrderLine).where(col(OrderLine.bundle_line_id).in_([line.id for line, _ in own]))
    ):
        parts[part.bundle_line_id or 0].append(part)
    line_ids = {line.id for line, _ in own} | {p.id for held in parts.values() for p in held}
    goods = dict(
        (
            await db.exec(
                select(OrderLine.order_id, func.sum(OrderLine.quantity * OrderLine.price))
                .where(col(OrderLine.order_id).in_({order.id for _, order in own}))
                .group_by(col(OrderLine.order_id))
            )
        ).all()
    )
    sent: dict[int, _Sent] = defaultdict(_Sent)
    for line_id, quantity, change, unit_cost in await db.exec(
        select(
            col(ShipmentLine.order_line_id),
            col(ShipmentLine.quantity),
            col(Adjustment.change),
            col(Adjustment.unit_cost),
        )
        .join(Shipment, col(Shipment.id) == ShipmentLine.shipment_id)
        .outerjoin(Adjustment, col(Adjustment.shipment_line_id) == ShipmentLine.id)
        .where(
            col(ShipmentLine.order_line_id).in_(line_ids), Shipment.status == ShipmentStatus.SHIPPED
        )
    ):
        sent[line_id].shipped += quantity
        if not unit_cost:
            sent[line_id].uncosted += 1
        else:
            sent[line_id].cost -= change * unit_cost
    for line_id, quantity, change, unit_cost in await db.exec(
        select(
            col(ShipmentLine.order_line_id),
            col(ReturnLine.quantity),
            col(Adjustment.change),
            col(Adjustment.unit_cost),
        )
        .join(ShipmentLine, col(ShipmentLine.id) == ReturnLine.shipment_line_id)
        .outerjoin(
            Adjustment,
            and_(
                col(Adjustment.return_line_id) == ReturnLine.id,
                col(Adjustment.kind) == AdjustmentKind.RETURN,
            ),
        )
        .where(col(ShipmentLine.order_line_id).in_(line_ids))
    ):
        sent[line_id].returned += quantity
        sent[line_id].cost -= (change or 0) * (unit_cost or 0)

    def sent_of(line: OrderLine) -> _Sent:
        """What was sent of ``line``: a bundle's, as many whole bundles as its
        parts sent, and what they cost."""
        held = parts.get(line.id)
        if not held:
            return sent[line.id]
        each = [(sent[part.id], part.quantity) for part in held]
        shipped = min(of.shipped / quantity for of, quantity in each)
        kept = min((of.shipped - of.returned) / quantity for of, quantity in each)
        return _Sent(
            shipped=units.scaled(line.quantity, shipped, down=True),
            returned=units.scaled(line.quantity, shipped - kept, down=True),
            cost=sum((of.cost for of, _ in each), Decimal(0)),
            uncosted=sum(of.uncosted for of, _ in each),
        )

    for line, order in own:
        of = sent_of(line)
        live = order.status in (OrderStatus.OPEN, OrderStatus.SHIPPED)
        # What it sells now; a pending or cancelled order sold what it shipped.
        selling = orders.selling(line) if live else of.shipped
        groups = found[line.item_id]
        figures = groups.wholesale if order.wholesale else groups.retail
        figures.sold += min(line.quantity, max(selling, of.shipped))
        figures.shipped += of.shipped - of.returned
        if order.status is OrderStatus.OPEN and line.id in parts:
            figures.bundles_committed += max(selling - of.shipped, Decimal(0))
        if line.bundle_line_id is not None:
            continue
        if (order.currency or currency) != currency:
            figures.left_out |= {order.id}
            continue
        whole = goods.get(order.id) or Decimal(0)
        net = Decimal(1) if whole == 0 else 1 - order.discount / whole
        earned = max(min(selling, of.shipped - of.returned), Decimal(0))
        figures.revenue += (earned * line.price * net).quantize(PLACES)
        figures.cost += of.cost.quantize(PLACES)
        figures.uncosted += of.uncosted
    return found


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
    on_order: OnOrder
    #: Over every window, in :class:`UsageWindow` order.
    usage: list[Usage]
    used_in: list[UsedIn]
    sales: SalesGroups
    #: Whether it is a bundle's variation, which holds no stock of its own.
    bundle: bool = False

    @property
    def stock(self) -> Stock:
        return sum((at.stock for at in self.locations.values()), Stock())

    @property
    def committed(self) -> Decimal:
        return self.sales.whole.bundles_committed if self.bundle else self.stock.committed

    @property
    def available(self) -> Decimal:
        return (self.potential or Decimal(0)) if self.bundle else self.stock.available


@dataclass(frozen=True)
class Totals:
    """Several items' figures together, such as a product's variations'."""

    on_hand: Decimal
    consigned: Decimal
    committed: Decimal
    available: Decimal
    #: None when none of them has any.
    potential: Decimal | None
    made: Decimal
    on_order: OnOrder
    sales: SalesGroups


def total(figures: Iterable[Summary]) -> Totals:
    """``figures`` added up."""
    held = list(figures)
    potentials = [each.potential for each in held if each.potential is not None]
    return Totals(
        on_hand=sum((each.stock.on_hand for each in held), Decimal(0)),
        consigned=sum((each.stock.consigned for each in held), Decimal(0)),
        committed=sum((each.committed for each in held), Decimal(0)),
        available=sum((each.available for each in held), Decimal(0)),
        potential=sum(potentials, Decimal(0)) if potentials else None,
        made=sum((each.made for each in held), Decimal(0)),
        on_order=OnOrder(
            sum((each.on_order.quantity for each in held), Decimal(0)),
            any(each.on_order.overdue for each in held),
        ),
        sales=sum((each.sales for each in held), SalesGroups()),
    )


async def _could_make(
    db: AsyncSession, item_ids: list[int], bundles: dict[int, int], today: date
) -> dict[int, dict[int, Decimal]]:
    """What each item with a recipe that has lines could make at every
    location, by item and then by location; a bundle's variations, of
    ``bundles`` by their product, from what their bundle holds."""
    found = (
        await db.exec(
            select(Recipe).where(
                or_(
                    col(Recipe.base_id).in_(item_ids),
                    col(Recipe.variation_id).in_(item_ids),
                    col(Recipe.product_id).in_(set(bundles.values())),
                )
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
        at: dict[int, Decimal] = {}
        for output in (
            [recipe.base_id or recipe.variation_id or 0]
            if recipe.product_id is None
            else [
                variation_id
                for variation_id, product_id in bundles.items()
                if product_id == recipe.product_id
            ]
        ):
            could[output] = at
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
    bundles = dict(
        (
            await db.exec(
                select(Variation.id, Variation.product_id)
                .join(Product, col(Product.id) == Variation.product_id)
                .where(col(Variation.id).in_(ids), col(Product.bundle))
            )
        ).all()
    )
    could = await _could_make(db, ids, bundles, today)
    ordered = await on_order(db, ids, today)
    usage = await _usage(db, ids, today)
    used_in = await _used_in(db, ids)
    sold = await sales(db, ids)
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
            on_order=ordered.get(item_id, OnOrder()),
            usage=usage[item_id],
            used_in=used_in[item_id],
            sales=sold[item_id],
            bundle=item_id in bundles,
        )
    for item_id, location_id, dated_on in counted:
        summary = found[item_id]
        at = summary.locations.setdefault(location_id, AtLocation())
        at.last_stocktake = dated_on
        if item_id in could and at.could_make is None:
            at.could_make = could[item_id][location_id]
        summary.last_stocktake = max(dated_on, summary.last_stocktake or dated_on)
    return found
