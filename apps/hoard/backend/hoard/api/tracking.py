"""The tasks that track production runs and purchases: how many are tracked,
and the events waiting to be sent to Initiative (``hoard.tracking``). An
administrator may send the waiting events now, rather than when they are next
due."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, update
from sqlmodel import col, select

from hoard import tracking
from hoard.api.deps import AdminDep
from hoard.records import OutboxEvent, TaskLink
from seneschal_core.api import OrganizationSessionDep, PluginDep, WriteSessionDep
from seneschal_core.db import OrganizationSession

router = APIRouter(prefix="/tracking", tags=["tracking"])


class TrackingRead(BaseModel):
    """What is tracked on Initiative's tasks, and what waits to be sent."""

    #: Production runs a task tracks.
    runs: int
    #: Purchases a task tracks.
    purchases: int
    #: Events waiting to be sent.
    waiting: int
    #: Of those, the ones Initiative could not take yet.
    failing: int
    #: When the first of those is tried again.
    retry_at: datetime | None


async def _read(db: OrganizationSession) -> TrackingRead:
    runs, purchases = (
        await db.exec(
            select(func.count(col(TaskLink.run_id)), func.count(col(TaskLink.purchase_id)))
        )
    ).one()
    failed = col(OutboxEvent.attempts) > 0
    waiting, failing, retry_at = (
        await db.exec(
            select(
                func.count(),
                func.count().filter(failed),
                func.min(OutboxEvent.next_attempt_at).filter(failed),
            ).select_from(OutboxEvent)
        )
    ).one()
    return TrackingRead(
        runs=runs, purchases=purchases, waiting=waiting, failing=failing, retry_at=retry_at
    )


@router.get("")
async def read_tracking(db: OrganizationSessionDep) -> TrackingRead:
    """How many runs and purchases are tracked, and the events waiting."""
    return await _read(db)


@router.post("/send", responses={403: {"description": "ADMIN_ONLY, READ_ONLY"}})
async def send_tracking_events(
    _admin: AdminDep, db: WriteSessionDep, plugin: PluginDep
) -> TrackingRead:
    """Send the waiting events now, and answer what still waits."""
    await db.execute(update(OutboxEvent).values(next_attempt_at=func.now()))
    await db.commit()
    await tracking.deliver(plugin, db.organization_id)
    return await _read(db)
