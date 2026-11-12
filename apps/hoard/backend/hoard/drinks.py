"""The reads the Drinks setup's widgets draw: waste by reason, what completed
production runs planned against what they made, and the allergen matrix
(``hoard.declarations``).

Waste is stock taken out by hand with a reason, other than a correction. The
two reads over time look back ``days`` days, :data:`DAYS` when a caller does
not say."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.declarations import Declarations
from hoard.events import item_name
from hoard.records import (
    PLACES,
    Adjustment,
    AdjustmentKind,
    AdjustmentReason,
    Allergen,
    Base,
    DefaultReason,
    Material,
    Product,
    ProductionRun,
    ProductStatus,
    Recipe,
    RunStatus,
    Unit,
)
from seneschal_core.platform import EndpointCall, organization_session

#: How many days back a read looks when its caller does not say.
DAYS = 30


def _since(call: EndpointCall) -> datetime:
    days = call.params.get("days")
    back = days if isinstance(days, int) and days > 0 else DAYS
    return datetime.now(UTC) - timedelta(days=back)


async def _unit(db: AsyncSession, item_id: int) -> str | None:
    """The unit a material's or a base's stock is counted in; a variation's is
    counted in each, which has none."""
    for kind in (Material, Base):
        found = await db.get(kind, item_id)
        if found is not None:
            unit = await db.get(Unit, found.tracking_unit_id)
            return unit.name if unit else None
    return None


async def waste(call: EndpointCall) -> dict[str, Any]:
    """Answer ``waste``: what was thrown away over the last ``days``, by reason
    in the order they were made, each reason's items the most first. A reason nobody has named is answered
    by its key."""
    since = _since(call).date()
    wasted = -func.sum(Adjustment.change)
    query = (
        select(
            func.coalesce(AdjustmentReason.name, AdjustmentReason.key),
            Adjustment.item_id,
            wasted,
        )
        .join(AdjustmentReason, col(AdjustmentReason.id) == Adjustment.reason_id)
        .where(
            Adjustment.kind == AdjustmentKind.MANUAL,
            Adjustment.change < 0,
            Adjustment.dated_on >= since,
            or_(
                col(AdjustmentReason.key).is_(None),
                col(AdjustmentReason.key) != DefaultReason.CORRECTION.value,
            ),
        )
        .group_by(col(AdjustmentReason.id), col(Adjustment.item_id))
        .order_by(col(AdjustmentReason.id), wasted.desc())
    )
    columns: dict[str, list[Any]] = {"reason": [], "item": [], "unit": [], "quantity": []}
    async with organization_session(call) as db:
        for reason, item_id, quantity in (await db.exec(query)).all():
            columns["reason"].append(reason)
            columns["item"].append(await item_name(db, item_id))
            columns["unit"].append(await _unit(db, item_id))
            columns["quantity"].append(str(quantity))
    return columns


async def planned_against_actual(call: EndpointCall) -> dict[str, Any]:
    """Answer ``yield``: each run completed over the last ``days``, the latest
    first, with what its batches of its recipe planned to make. A run whose
    recipe is gone planned nothing anyone can say."""
    since = _since(call).date()
    query = (
        select(ProductionRun, col(Recipe.batch_quantity))
        .outerjoin(Recipe, col(Recipe.id) == ProductionRun.recipe_id)
        .where(
            ProductionRun.status == RunStatus.COMPLETED, col(ProductionRun.completed_on) >= since
        )
        .order_by(col(ProductionRun.completed_on).desc(), col(ProductionRun.id).desc())
    )
    columns: dict[str, list[Any]] = {
        key: [] for key in ("code", "item", "completed_on", "unit", "planned", "actual")
    }
    async with organization_session(call) as db:
        for run, batch_quantity in (await db.exec(query)).all():
            planned: Decimal | None = (
                None
                if batch_quantity is None
                else (run.planned_batches * batch_quantity).quantize(PLACES)
            )
            columns["code"].append(run.code)
            columns["item"].append(await item_name(db, run.item_id))
            columns["completed_on"].append(run.completed_on and run.completed_on.isoformat())
            columns["unit"].append(await _unit(db, run.item_id))
            columns["planned"].append(None if planned is None else str(planned))
            columns["actual"].append(str(run.actual_quantity))
    return columns


async def allergen_matrix(call: EndpointCall) -> dict[str, Any]:
    """Answer ``allergen-matrix``: each active product by name, and which
    allergens it contains (``hoard.declarations``)."""
    columns: dict[str, list[Any]] = {"product": [], **{allergen.value: [] for allergen in Allergen}}
    async with organization_session(call) as db:
        declarations = Declarations(db)
        products = await db.exec(
            select(Product)
            .where(Product.status == ProductStatus.ACTIVE)
            .order_by(col(Product.name), col(Product.id))
        )
        for product in products.all():
            contains = set((await declarations.product(product)).allergens)
            columns["product"].append(product.name)
            for allergen in Allergen:
                columns[allergen.value].append(allergen in contains)
    return columns
