"""Production planning: what to make next, the materials it takes, the hours
it takes, what making costs, what is about to expire, and what sold by week.

Everything here reads Hoard's own records: recipes, runs, lots, purchases and
costs. Orders are read only for demand: what a line sells now
(``hoard.orders.SELLING``), so a refund is not counted, on every order but a
cancelled one, with a bundle counted as the parts it sells.

:func:`plan` is the computation the first three reads share:

* **Expected sales.** Each variation's daily sales over the
  :data:`RATE_DAYS` days before today, times the days until a run started
  today is ready to sell: :data:`HORIZON_DAYS` plus its product's cure days.
  Once Hoard holds a year of orders, and the :data:`RATE_DAYS` days before
  it, the rate is scaled by how those days last year compared with the
  :data:`RATE_DAYS` days before them.
* **Suggested runs.** What a variation is short is its expected sales less
  what is available (``hoard.summary``), what is curing (its lots on
  a curing hold) and what its planned and started runs will make, in whole
  batches of its recipe. A base is short by what the planned, started and
  suggested runs take of it, less the same three; bases are worked out from
  the outermost in, so a base inside a base counts the runs of the one
  holding it.
* **Runs.** The planned and started runs, by their own lines, and the
  suggested ones, by their recipe's lines times their batches. Each is needed
  by its deadline, or today when it has none; a suggested base run is needed
  when the earliest run taking it is.

The reads answer an endpoint call (``hoard.endpoints``):

* :func:`make_next`: the suggested runs.
* :func:`materials_to_order`: each material the runs take more of than is
  available and on order, with its supplier, the day it is needed and the
  day to order it by: that day less the supplier's median days from ordering
  to receiving over its purchases received in full. A supplier with none has
  no order-by day.
* :func:`hours_ahead`: the hours of work each of the next
  :data:`HOURS_WEEKS` weeks holds: planned and started runs in the week of
  their deadline (the first, for one overdue or with none), suggested runs in
  the first.
* :func:`cost_to_make`: each variation's cost per unit over its last two
  completed runs, beside the price its store sells it for.
* :func:`expiring_soon`: the lots holding stock that expire within
  :data:`EXPIRY_DAYS` days, or have expired.
* :func:`sales_by_week`: what each variation sold in each of the last
  :data:`SALES_WEEKS` weeks, Monday to Sunday.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import orders, recipes, summary
from hoard import recall as recalls
from hoard.events import item_name
from hoard.holds import held_lots
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    DefaultHoldReason,
    Hold,
    HoldReason,
    ItemStatus,
    Listing,
    Lot,
    Material,
    Order,
    OrderLine,
    OrderStatus,
    Product,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    PurchaseLine,
    Recipe,
    RunStatus,
    Variation,
)
from hoard.reports import currency, unit_name
from seneschal_core.initiative_api import InitiativeUnavailable
from seneschal_core.platform import EndpointCall, organization_session

#: The days a run takes to be ready to sell, before its cure days.
HORIZON_DAYS = 14
#: The days the sales rate is taken over.
RATE_DAYS = 28
#: A year, in whole weeks, so last year's days fall on the same weekdays.
YEAR_DAYS = 364
#: The weeks the hours ahead cover.
HOURS_WEEKS = 4
#: How soon a lot expires to be listed as expiring soon.
EXPIRY_DAYS = 30
#: The weeks sales by week cover, this one included.
SALES_WEEKS = 12

#: The runs that will make something: planned, and started.
OPEN_RUNS = (RunStatus.NOT_STARTED, RunStatus.IN_PROGRESS)

#: Hours, to two places.
HOUR_PLACES = Decimal("0.01")


@dataclass
class Run:
    """A run that will take what its lines hold: a planned or started run, or
    a suggested one."""

    item_id: int
    #: All its batches' minutes together.
    minutes: Decimal
    #: The day it is needed by: its deadline, or today when it has none.
    needed_on: date
    #: Its deadline; a suggested run has none.
    deadline: date | None
    suggested: bool
    #: What it takes of each item, by item id.
    lines: dict[int, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class Suggestion:
    """A variation or a base to make: what is needed of it, what there is,
    and the whole batches of its recipe that make up the rest."""

    item_id: int
    #: Expected sales of a variation; what the runs take of a base.
    needed: Decimal
    available: Decimal
    curing: Decimal
    #: What its planned and started runs will make.
    planned: Decimal
    short: Decimal
    batches: int
    minutes: Decimal
    base: bool


@dataclass
class Plan:
    """What to make, and every run that will take something."""

    suggestions: list[Suggestion]
    runs: list[Run]


async def _sales(db: AsyncSession, since: date) -> dict[int, dict[date, Decimal]]:
    """What each item sold on each day since ``since``: what each line sells
    now, on every order but a cancelled one, a bundle counted as its parts."""
    rows = await db.exec(
        select(col(OrderLine.item_id), col(Order.ordered_on), func.sum(orders.SELLING))
        .join(Order, col(Order.id) == OrderLine.order_id)
        .where(
            Order.status != OrderStatus.CANCELLED,
            col(Order.ordered_on) >= since,
            orders.ships_itself(),
        )
        .group_by(col(OrderLine.item_id), col(Order.ordered_on))
    )
    found: dict[int, dict[date, Decimal]] = defaultdict(dict)
    for item_id, day, quantity in rows.all():
        found[item_id][day] = Decimal(quantity)
    return found


def _sold(days: dict[date, Decimal], start: date, end: date) -> Decimal:
    """What sold from ``start`` up to ``end``."""
    return sum((quantity for day, quantity in days.items() if start <= day < end), Decimal(0))


def expected(days: dict[date, Decimal], today: date, horizon: int, seasonal: bool) -> Decimal:
    """What a variation is expected to sell over the ``horizon`` days from
    ``today``, from what it sold each day: the rate of the :data:`RATE_DAYS`
    days before today, and, when ``seasonal``, scaled by how the same days
    last year compared with the :data:`RATE_DAYS` days before them. Each
    product is taken before its quotient, so whole sales stay whole."""
    recent = _sold(days, today - timedelta(RATE_DAYS), today)
    then = today - timedelta(YEAR_DAYS)
    before = _sold(days, then - timedelta(RATE_DAYS), then)
    if seasonal and before > 0:
        # The rate, times the horizon, times last year's rate ahead over its rate before.
        found = recent * _sold(days, then, then + timedelta(horizon)) / before
    else:
        found = recent * horizon / RATE_DAYS
    return found.quantize(PLACES)


async def _seasonal(db: AsyncSession, today: date) -> bool:
    """Whether Hoard holds orders from a year and :data:`RATE_DAYS` days ago."""
    first = (
        await db.exec(
            select(func.min(Order.ordered_on)).where(Order.status != OrderStatus.CANCELLED)
        )
    ).one()
    return first is not None and first <= today - timedelta(YEAR_DAYS + RATE_DAYS)


async def _curing(db: AsyncSession) -> dict[int, Decimal]:
    """What each item's lots on a curing hold hold."""
    rows = await db.exec(
        select(Lot.item_id, func.sum(func.greatest(Lot.balance, 0)))
        .join(Hold, col(Hold.lot_id) == Lot.id)
        .join(HoldReason, col(HoldReason.id) == Hold.reason_id)
        .where(col(Hold.ended).is_(None), HoldReason.key == DefaultHoldReason.CURING.value)
        .group_by(col(Lot.item_id))
    )
    return defaultdict(Decimal, {item_id: Decimal(held) for item_id, held in rows.all()})


async def plan(db: AsyncSession, today: date) -> Plan:
    """What to make next, and the runs that will take something: see the
    module's docstring."""
    found = (
        await db.exec(
            select(Recipe).where(
                (col(Recipe.variation_id).is_not(None)) | (col(Recipe.base_id).is_not(None))
            )
        )
    ).all()
    by_id = {recipe.id: recipe for recipe in found}
    made_by = {made: recipe for recipe in found if (made := recipe.variation_id or recipe.base_id)}
    recipe_lines = await recipes.lines_of(db, by_id)
    curing = await _curing(db)

    open_runs = (
        await db.exec(select(ProductionRun).where(col(ProductionRun.status).in_(OPEN_RUNS)))
    ).all()
    run_lines: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for line in await db.exec(
        select(ProductionRunLine).where(
            col(ProductionRunLine.run_id).in_([run.id for run in open_runs])
        )
    ):
        run_lines[line.run_id][line.item_id] += line.quantity
    runs: list[Run] = []
    planned: dict[int, Decimal] = defaultdict(Decimal)
    for run in open_runs:
        recipe = by_id.get(run.recipe_id or 0) or made_by.get(run.item_id)
        if recipe is not None:
            planned[run.item_id] += run.planned_batches * recipe.batch_quantity
        runs.append(
            Run(
                item_id=run.item_id,
                minutes=run.planned_batches * Decimal((recipe.minutes or 0) if recipe else 0),
                needed_on=run.deadline_on or today,
                deadline=run.deadline_on,
                suggested=False,
                lines=dict(run_lines[run.id]),
            )
        )

    suggestions: list[Suggestion] = []

    async def suggest(item_id: int, needed: Decimal, needed_on: date, base: bool) -> None:
        recipe = made_by[item_id]
        available = (await summary.stock_at(db, item_id)).available
        short = needed - available - curing[item_id] - planned[item_id]
        if short <= 0:
            return
        batches = math.ceil(short / recipe.batch_quantity)
        minutes = batches * Decimal(recipe.minutes or 0)
        suggestions.append(
            Suggestion(
                item_id,
                needed,
                available,
                curing[item_id],
                planned[item_id],
                short,
                batches,
                minutes,
                base,
            )
        )
        runs.append(
            Run(
                item_id=item_id,
                minutes=minutes,
                needed_on=needed_on,
                deadline=None,
                suggested=True,
                lines={line.item_id: line.quantity * batches for line in recipe_lines[recipe.id]},
            )
        )

    variations = (
        await db.exec(
            select(Variation.id, Product.cure_days)
            .join(Product, col(Product.id) == Variation.product_id)
            .where(Product.status == ItemStatus.ACTIVE, col(Product.bundle).is_(False))
        )
    ).all()
    sales = await _sales(db, today - timedelta(YEAR_DAYS + RATE_DAYS))
    seasonal = await _seasonal(db, today)
    for variation_id, cure_days in variations:
        if variation_id in made_by:
            horizon = HORIZON_DAYS + (cure_days or 0)
            needed = expected(sales.get(variation_id, {}), today, horizon, seasonal)
            await suggest(variation_id, needed, today, base=False)

    # The bases, each once every base holding it has been worked out.
    bases = {recipe.base_id for recipe in found if recipe.base_id is not None}
    inside = {
        base_id: [
            line.item_id for line in recipe_lines[made_by[base_id].id] if line.item_id in bases
        ]
        for base_id in bases
    }
    holders = Counter(inner for base_id in bases for inner in inside[base_id])
    ready = sorted((base_id for base_id in bases if not holders[base_id]), reverse=True)
    while ready:
        base_id = ready.pop()
        taking = [run for run in runs if run.lines.get(base_id)]
        if taking:
            needed = sum((run.lines[base_id] for run in taking), Decimal(0))
            await suggest(base_id, needed, min(run.needed_on for run in taking), base=True)
        for inner in inside[base_id]:
            holders[inner] -= 1
            if not holders[inner]:
                ready.append(inner)
    return Plan(suggestions, runs)


@dataclass(frozen=True)
class MaterialShort:
    """A material the runs take more of than is available and on order."""

    material_id: int
    supplier_contact_id: int | None
    short: Decimal
    needed_on: date
    #: The day to order it by; none when its supplier has received nothing yet.
    order_by: date | None


async def lead_days(db: AsyncSession) -> dict[int, int]:
    """Each supplier's median days from ordering to receiving, over its
    purchases received in full, rounded up. A purchase is received on the day
    its last line was."""
    received = func.max(Adjustment.created_at)
    rows = await db.exec(
        select(col(Purchase.supplier_contact_id), col(Purchase.dated_on), received)
        .join(PurchaseLine, col(PurchaseLine.purchase_id) == Purchase.id)
        .outerjoin(
            Adjustment,
            and_(
                col(Adjustment.purchase_line_id) == PurchaseLine.id,
                col(Adjustment.kind) == AdjustmentKind.PURCHASE,
            ),
        )
        .where(col(Purchase.supplier_contact_id).is_not(None))
        .group_by(col(Purchase.id), col(Purchase.supplier_contact_id), col(Purchase.dated_on))
        .having(func.bool_and(col(PurchaseLine.received_on).is_not(None)), received.is_not(None))
    )
    days: dict[int | None, list[int]] = defaultdict(list)
    for supplier_id, ordered_on, received_at in rows.all():
        days[supplier_id].append(max((received_at.astimezone(UTC).date() - ordered_on).days, 0))
    return {
        supplier_id: math.ceil(statistics.median(each))
        for supplier_id, each in days.items()
        if supplier_id is not None
    }


async def materials_short(db: AsyncSession, runs: Iterable[Run]) -> list[MaterialShort]:
    """Each material ``runs`` take more of than is available and on order,
    the soonest to order first."""
    needed: dict[int, Decimal] = defaultdict(Decimal)
    needed_on: dict[int, date] = {}
    for run in runs:
        for item_id, quantity in run.lines.items():
            needed[item_id] += quantity
            needed_on[item_id] = min(needed_on.get(item_id, run.needed_on), run.needed_on)
    materials = (
        await db.exec(
            select(Material).where(col(Material.id).in_(needed), col(Material.inventoriable))
        )
    ).all()
    on_order = dict(
        (
            await db.exec(
                select(PurchaseLine.material_id, func.sum(PurchaseLine.tracking_quantity))
                .where(col(PurchaseLine.received_on).is_(None))
                .group_by(col(PurchaseLine.material_id))
            )
        ).all()
    )
    lead = await lead_days(db)
    found: list[tuple[str, MaterialShort]] = []
    for material in materials:
        short = (
            needed[material.id]
            - (await summary.stock_at(db, material.id)).available
            - Decimal(on_order.get(material.id, 0))
        )
        if short <= 0:
            continue
        on = needed_on[material.id]
        days = lead.get(material.supplier_contact_id or 0)
        order_by = None if days is None else on - timedelta(days)
        found.append(
            (
                material.name,
                MaterialShort(material.id, material.supplier_contact_id, short, on, order_by),
            )
        )
    found.sort(key=lambda each: (each[1].order_by or each[1].needed_on, each[0].casefold()))
    return [shortfall for _, shortfall in found]


def _amount(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(PLACES))


def _hours(minutes: Decimal) -> str:
    return str((minutes / 60).quantize(HOUR_PLACES))


async def make_next(call: EndpointCall) -> dict[str, Any]:
    """Answer ``make-next``: each variation, then each base, to make, by
    name, with the whole batches that make what it is short and the hours
    they take."""
    keys = (
        "kind",
        "item",
        "unit",
        "needed",
        "available",
        "curing",
        "planned",
        "short",
        "batches",
        "hours",
    )
    columns: dict[str, list[Any]] = {key: [] for key in keys}
    async with organization_session(call) as db:
        named = [
            (suggestion, await item_name(db, suggestion.item_id))
            for suggestion in (await plan(db, date.today())).suggestions
        ]
        named.sort(key=lambda each: (each[0].base, each[1].casefold()))
        for suggestion, name in named:
            columns["kind"].append("base" if suggestion.base else "product")
            columns["item"].append(name)
            columns["unit"].append(await unit_name(db, suggestion.item_id))
            for key in ("needed", "available", "curing", "planned", "short"):
                columns[key].append(_amount(getattr(suggestion, key)))
            columns["batches"].append(suggestion.batches)
            columns["hours"].append(_hours(suggestion.minutes))
    return columns


async def materials_to_order(call: EndpointCall) -> dict[str, Any]:
    """Answer ``materials-to-order``: each material the planned, started and
    suggested runs take more of than is available and on order, the soonest
    to order first. A supplier is named as the community's contacts name it,
    when Initiative answers."""
    keys = ("material", "unit", "supplier", "short", "needed_on", "order_by")
    columns: dict[str, list[Any]] = {key: [] for key in keys}
    async with organization_session(call) as db:
        shortfalls = await materials_short(db, (await plan(db, date.today())).runs)
        suppliers = {s.supplier_contact_id for s in shortfalls if s.supplier_contact_id}
        try:
            parties = await recalls.parties(call.plugin.api, call.claims.community_ref, suppliers)
        except InitiativeUnavailable:
            parties = {}
        for shortfall in shortfalls:
            party = parties.get(shortfall.supplier_contact_id or 0)
            columns["material"].append(await item_name(db, shortfall.material_id))
            columns["unit"].append(await unit_name(db, shortfall.material_id))
            columns["supplier"].append(party.name if party else None)
            columns["short"].append(_amount(shortfall.short))
            columns["needed_on"].append(shortfall.needed_on.isoformat())
            columns["order_by"].append(shortfall.order_by and shortfall.order_by.isoformat())
    return columns


def weekly_minutes(runs: Iterable[Run], today: date) -> list[tuple[Decimal, Decimal]]:
    """The minutes of planned and started runs, and of suggested ones, in
    each of the :data:`HOURS_WEEKS` weeks from today."""
    weeks = [(Decimal(0), Decimal(0)) for _ in range(HOURS_WEEKS)]
    for run in runs:
        due = run.deadline if run.deadline and run.deadline > today else today
        week = (due - today).days // 7
        if week < HOURS_WEEKS:
            planned, suggested = weeks[week]
            weeks[week] = (
                (planned, suggested + run.minutes)
                if run.suggested
                else (planned + run.minutes, suggested)
            )
    return weeks


async def hours_ahead(call: EndpointCall) -> dict[str, Any]:
    """Answer ``hours-ahead``: the hours of planned and started runs, and of
    suggested ones, in each of the next :data:`HOURS_WEEKS` weeks, each named
    by its first day."""
    today = date.today()
    async with organization_session(call) as db:
        weeks = weekly_minutes((await plan(db, today)).runs, today)
    return {
        "week": [(today + timedelta(weeks=week)).isoformat() for week in range(HOURS_WEEKS)],
        "planned": [_hours(planned) for planned, _ in weeks],
        "suggested": [_hours(suggested) for _, suggested in weeks],
    }


async def cost_to_make(call: EndpointCall) -> dict[str, Any]:
    """Answer ``cost-to-make``: each active product's variation with a
    completed run that made something, by name, with what one unit of its
    last such run cost and of the run before, the change between them, the
    price its store sells it for and the margin that leaves, in the
    organization's currency. Only a member who sees costs reads it."""
    keys = ("product", "last", "previous", "change", "price", "margin")
    columns: dict[str, list[Any]] = {key: [] for key in keys}
    async with organization_session(call) as db:
        made = await db.exec(
            select(col(ProductionRun.item_id), col(Adjustment.unit_cost))
            .join(Variation, col(Variation.id) == ProductionRun.item_id)
            .join(Product, col(Product.id) == Variation.product_id)
            .join(
                Adjustment,
                and_(
                    col(Adjustment.production_run_id) == ProductionRun.id,
                    col(Adjustment.production_run_line_id).is_(None),
                ),
            )
            .where(
                ProductionRun.status == RunStatus.COMPLETED,
                Product.status == ItemStatus.ACTIVE,
            )
            .order_by(col(ProductionRun.completed_on).desc(), col(ProductionRun.id).desc())
        )
        costs: dict[int, list[Decimal]] = defaultdict(list)
        for item_id, unit_cost in made.all():
            costs[item_id].append(unit_cost)
        prices = await db.exec(
            select(col(Listing.item_id), col(Listing.price))
            .where(col(Listing.item_id).in_(costs), col(Listing.price).is_not(None))
            .order_by(col(Listing.id).desc())
        )
        # The earliest listing's price, where several sell the variation.
        price_of = dict(prices.all())
        named = sorted(
            [(await item_name(db, item_id), item_id) for item_id in costs],
            key=lambda each: (each[0].casefold(), each[1]),
        )
        for name, item_id in named:
            last, *rest = costs[item_id]
            previous = rest[0] if rest else None
            price = price_of.get(item_id)
            columns["product"].append(name)
            columns["last"].append(_amount(last))
            columns["previous"].append(_amount(previous))
            columns["change"].append(None if previous is None else _amount(last - previous))
            columns["price"].append(_amount(price))
            columns["margin"].append(_amount((price - last) / price) if price else None)
        return {**columns, "currency": await currency(db)}


async def expiring_soon(call: EndpointCall) -> dict[str, Any]:
    """Answer ``expiring-soon``: the lots of materials, bases and products
    holding stock that expire within :data:`EXPIRY_DAYS` days or have expired,
    the soonest first, with what is left of each and whether it is on hold."""
    today = date.today()
    keys = ("lot", "item", "unit", "expires_on", "days_left", "left", "on_hold")
    columns: dict[str, list[Any]] = {key: [] for key in keys}
    async with organization_session(call) as db:
        lots = await db.exec(
            select(Lot, col(Lot.id).in_(held_lots()))
            .where(
                col(Lot.expires_on) <= today + timedelta(EXPIRY_DAYS),
                col(Lot.balance) > 0,
            )
            .order_by(col(Lot.expires_on), col(Lot.id))
        )
        for lot, on_hold in lots.all():
            expires_on = lot.expires_on or today
            columns["lot"].append(lot.number)
            columns["item"].append(await item_name(db, lot.item_id))
            columns["unit"].append(await unit_name(db, lot.item_id))
            columns["expires_on"].append(expires_on.isoformat())
            columns["days_left"].append((expires_on - today).days)
            columns["left"].append(_amount(lot.balance))
            columns["on_hold"].append(bool(on_hold))
    return columns


async def sales_by_week(call: EndpointCall) -> dict[str, Any]:
    """Answer ``sales-by-week``: what each variation that sold any sold in
    each of the last :data:`SALES_WEEKS` weeks, Monday to Sunday, this one
    included: by name, then week."""
    today = date.today()
    first = today - timedelta(days=today.weekday(), weeks=SALES_WEEKS - 1)
    weeks = [first + timedelta(weeks=week) for week in range(SALES_WEEKS)]
    columns: dict[str, list[Any]] = {"product": [], "week": [], "units": []}
    async with organization_session(call) as db:
        sales = await _sales(db, first)
        named = sorted(
            [(await item_name(db, item_id), item_id) for item_id in sales],
            key=lambda each: (each[0].casefold(), each[1]),
        )
    for name, item_id in named:
        for week in weeks:
            columns["product"].append(name)
            columns["week"].append(week.isoformat())
            columns["units"].append(_amount(_sold(sales[item_id], week, week + timedelta(weeks=1))))
    return columns
