"""Auto-manufacture: a store order that claims stock makes what it sells, by
a production run that completes at once, when the variation, its product or
the organization says so: always, or only for what is out of stock. Such a
run records no events.

Each variation the order sells is made at the order's location, once for all
its lines, which name the run. Always makes what the lines sell; out of stock
makes only what is not available for them, what other open orders commit
counted. A bundle's own line is not made, its parts are; a variation with no
recipe is not made.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from hoard import orders, runs
from hoard.records import (
    AutoManufacture,
    Order,
    OrderLine,
    OrderStatus,
    OrganizationSettings,
    Product,
    Variation,
)
from seneschal_core.db import WriteSession


async def rule_for(db: WriteSession, variation: Variation) -> AutoManufacture:
    """Whether a store order makes ``variation``: its own say, else its
    product's, else the organization's."""
    if variation.auto_manufacture is not None:
        return variation.auto_manufacture
    product = await db.get_one(Product, variation.product_id)
    if product.auto_manufacture is not None:
        return product.auto_manufacture
    settings = (await db.exec(select(OrganizationSettings))).first()
    return AutoManufacture.OFF if settings is None else settings.auto_manufacture


async def on_claimed(db: WriteSession, order: Order) -> None:
    """Make what ``order``, a store order that has just claimed its stock,
    sells, where auto-manufacture says so."""
    found = await db.exec(
        select(OrderLine).where(OrderLine.order_id == order.id).order_by(col(OrderLine.id))
    )
    held = list(found.all())
    bundles = {line.bundle_line_id for line in held if line.bundle_line_id is not None}
    selling: dict[int, list[OrderLine]] = defaultdict(list)
    for line in held:
        if line.id not in bundles and line.production_run_id is None:
            selling[line.item_id].append(line)
    for item_id, lines in selling.items():
        variation = await db.get(Variation, item_id)
        if variation is None:
            continue
        rule = await rule_for(db, variation)
        if rule is AutoManufacture.OFF:
            continue
        wanted = sum((line.quantity for line in lines), Decimal(0))
        if rule is AutoManufacture.OUT_OF_STOCK:
            # What is available for this order: what others do not commit.
            free = await orders.available(db, item_id, order.location_id)
            if order.status is OrderStatus.OPEN:
                free += wanted
            wanted -= min(wanted, max(free, Decimal(0)))
        if wanted <= 0:
            continue
        run = await runs.make(
            db, item_id, order.location_id, wanted, date.today(), initiative_id=order.initiative_id
        )
        if run is None:
            continue
        for line in lines:
            line.production_run_id = run.id
    await db.flush()
