"""Tracking production runs and purchases with Initiative tasks.

* ``link-task``, public, which a community's automations call once they have
  made the task for a record: stores the :class:`~hoard.records.TaskLink` and
  writes Hoard's fields on the task (:func:`fields`).
* ``complete-run`` and ``receive-purchase``, the writes a task's buttons run
  (``viewer`` and ``subject`` name the reader and the task): the run is
  completed (``hoard.runs``), or every line of the purchase not yet received
  is received.
* Sending the event outbox (``hoard.events``) to Initiative: :func:`deliver`
  sends one organization's events in the order they were recorded, and
  :func:`deliver_forever`, run by the app's lifespan, calls it for an
  organization as soon as a commit has recorded events there, and for every
  organization once a minute. An event Initiative could not take is tried
  again later, waiting longer each time, and the events after it wait for it;
  one Initiative refuses is dropped. Before an event about a run or a
  purchase is sent, the fields on the record's task are written again and the
  event names the task linked now.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from fastapi import status
from sqlalchemy import event, func, text
from sqlalchemy.orm import Session
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import events, plugin_manifest, purchases, runs
from hoard.records import (
    OutboxEvent,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    PurchaseLine,
    Recipe,
    RunStatus,
    TaskLink,
)
from hoard.stock import StockRefused, on_hand
from seneschal_core import organizations
from seneschal_core.api import ErrorCode, Plugin, refuse
from seneschal_core.db import WriteSession
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable
from seneschal_core.platform import EndpointCall, write_session

logger = logging.getLogger(__name__)


class TrackingError(StrEnum):
    """Why a tracking write was refused."""

    #: No such run or purchase, or the task tracks none.
    NOT_FOUND = "NOT_FOUND"
    #: The task already tracks another run or purchase.
    TASK_TRACKS_ANOTHER = "TASK_TRACKS_ANOTHER"
    #: The run is already completed.
    RUN_COMPLETED = "RUN_COMPLETED"


#: The records a task tracks, by the name ``link-task`` and the events give them.
RECORDS: dict[str, type[ProductionRun] | type[Purchase]] = {
    "run": ProductionRun,
    "purchase": Purchase,
}
#: How often every organization's outbox is looked at, besides after a commit.
SWEEP_SECONDS = 60.0
#: The wait before an event is tried again, doubling with each attempt, up to
#: :data:`LONGEST_WAIT`.
FIRST_WAIT = timedelta(seconds=30)
LONGEST_WAIT = timedelta(hours=6)
#: Refusals that may pass: Initiative is busy, or the token it gave was not taken.
_LATER = frozenset({401, 408, 429})
#: The lock one process holds while it sends an organization's events.
_OUTBOX_LOCK = "SELECT pg_try_advisory_xact_lock(hashtext('hoard.event_outbox'), :organization)"


def _link_column(record: str) -> Any:
    return getattr(TaskLink, f"{record}_id")


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


async def _run_fields(db: AsyncSession, run: ProductionRun) -> dict[str, Any]:
    quantity = run.actual_quantity
    if quantity is None and run.recipe_id is not None:
        recipe = await db.get(Recipe, run.recipe_id)
        quantity = None if recipe is None else run.planned_batches * recipe.batch_quantity
    short = None
    if run.status is not RunStatus.COMPLETED:
        needed = await db.exec(
            select(ProductionRunLine.item_id, func.sum(ProductionRunLine.quantity))
            .where(ProductionRunLine.run_id == run.id)
            .group_by(col(ProductionRunLine.item_id))
        )
        lines = needed.all()
        if lines:
            short = 0
            for item_id, wanted in lines:
                if await on_hand(db, item_id, run.location_id) < wanted:
                    short += 1
    return {
        "hoard.record": run.code,
        "hoard.status": run.status.value,
        "hoard.item": await events.item_name(db, run.item_id),
        "hoard.quantity": None if quantity is None else _number(quantity),
        "hoard.materials": None if short is None else ("short" if short else "in_stock"),
        "hoard.short": short or None,
        "hoard.assignee": run.assignee_ref,
        "hoard.started": _day(run.started_on),
        "hoard.deadline": _day(run.deadline_on),
    }


async def _purchase_fields(db: AsyncSession, purchase: Purchase) -> dict[str, Any]:
    lines, received = (
        await db.exec(
            select(func.count(), func.count().filter(col(PurchaseLine.received))).where(
                PurchaseLine.purchase_id == purchase.id
            )
        )
    ).one()
    if lines and received == lines:
        state = "received"
    else:
        state = "partly_received" if received else "ordered"
    return {
        "hoard.record": purchase.code,
        "hoard.status": state,
        "hoard.ordered": _day(purchase.dated_on),
        "hoard.received": {"value": received, "max": lines} if lines else None,
    }


async def fields(db: AsyncSession, record: ProductionRun | Purchase) -> dict[str, Any]:
    """The fields Hoard keeps on the task tracking ``record``, as it stands;
    a None removes its field."""
    if isinstance(record, Purchase):
        return await _purchase_fields(db, record)
    return await _run_fields(db, record)


async def _write_fields(
    plugin: Plugin,
    community_ref: str,
    task_id: int,
    values: dict[str, Any],
    initiative_id: int | None,
) -> None:
    """Write ``values`` on the task. A refusal, such as for a task deleted
    since, is let go."""
    try:
        await plugin.api.write_metadata(
            community_ref, "task", task_id, values=values, narrowed_to=initiative_id
        )
    except InitiativeRefused as refused:
        logger.warning(
            "Initiative refused Hoard's fields on task %s (%s, %s)",
            task_id,
            refused.status_code,
            refused.detail,
        )


def _positive_id(params: Any, key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    return value


async def link_task(call: EndpointCall) -> dict[str, Any]:
    """Answer ``link-task``: its ``record``, ``record_id`` and ``task_id``.

    A record opened in another initiative than the call's is not found; one
    opened before Hoard kept its initiative is found from any. A
    record already tracked moves to the new task, and the old task's fields
    are taken off; a task tracks one record."""
    record = call.params.get("record")
    if not isinstance(record, str) or record not in RECORDS:
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    record_id = _positive_id(call.params, "record_id")
    task_id = _positive_id(call.params, "task_id")
    async with write_session(call) as db:
        found = await db.get(RECORDS[record], record_id)
        if found is None:
            raise refuse(status.HTTP_404_NOT_FOUND, TrackingError.NOT_FOUND)
        initiative_id = found.initiative_id or call.claims.initiative_id
        if call.claims.initiative_id not in (None, initiative_id):
            raise refuse(status.HTTP_404_NOT_FOUND, TrackingError.NOT_FOUND)
        held = (await db.exec(select(TaskLink).where(TaskLink.task_id == task_id))).first()
        if held is not None and getattr(held, f"{record}_id") != record_id:
            raise refuse(status.HTTP_409_CONFLICT, TrackingError.TASK_TRACKS_ANOTHER)
        link = (await db.exec(select(TaskLink).where(_link_column(record) == record_id))).first()
        before = None if link is None else link.task_id
        if link is None:
            link = TaskLink(organization_id=db.organization_id, task_id=task_id)
            setattr(link, f"{record}_id", record_id)
            db.add(link)
        else:
            link.task_id = task_id
        await db.flush()
        values = await fields(db, found)
        await db.commit()
    try:
        if before not in (None, task_id):
            cleared = dict.fromkeys(values)
            await _write_fields(call.plugin, call.claims.community_ref, before, cleared, None)
        await _write_fields(call.plugin, call.claims.community_ref, task_id, values, initiative_id)
    except InitiativeUnavailable:
        logger.warning("Hoard's fields wait for the next event about %s %s", record, record_id)
    return {"record": record, "record_id": record_id, "task_id": task_id}


async def _tracked(db: WriteSession, call: EndpointCall, record: str) -> int:
    """The id of the ``record`` the action's task tracks."""
    subject = call.claims.subject
    if subject is None or subject.type != "task":
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    column = _link_column(record)
    found = (
        await db.exec(select(column).where(TaskLink.task_id == subject.id, column.is_not(None)))
    ).first()
    if found is None:
        raise refuse(status.HTTP_404_NOT_FOUND, TrackingError.NOT_FOUND)
    return found


def _quantity(value: object) -> Decimal:
    quantity = None
    if isinstance(value, str | int) and not isinstance(value, bool):
        with suppress(InvalidOperation):
            quantity = Decimal(str(value).strip())
    if quantity is None or not quantity.is_finite() or quantity < 0:
        raise refuse(status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_INVALID)
    return quantity


async def complete_run(call: EndpointCall) -> dict[str, Any]:
    """Answer ``complete-run`` for the run the subject task tracks: completed
    today, at the actual quantity asked for."""
    actual = _quantity(call.params.get("actual_quantity"))
    async with write_session(call) as db:
        run = await db.get_one(ProductionRun, await _tracked(db, call, "run"))
        if run.status is RunStatus.COMPLETED:
            raise refuse(status.HTTP_409_CONFLICT, TrackingError.RUN_COMPLETED)
        try:
            await runs.complete(db, run, actual, date.today())
        except StockRefused as refused:
            raise refuse(status.HTTP_409_CONFLICT, refused.code) from None
        await db.commit()
    return {"run_id": run.id, "code": run.code, "status": run.status.value}


async def receive_purchase(call: EndpointCall) -> dict[str, Any]:
    """Answer ``receive-purchase`` for the purchase the subject task tracks:
    each line not yet received is received, into a lot of its own."""
    async with write_session(call) as db:
        purchase = await db.get_one(Purchase, await _tracked(db, call, "purchase"))
        waiting = await db.exec(
            select(PurchaseLine)
            .where(PurchaseLine.purchase_id == purchase.id, col(PurchaseLine.received).is_(False))
            .order_by(col(PurchaseLine.id))
        )
        for line in waiting.all():
            await purchases.receive_line(db, line)
        received = (
            await db.exec(
                select(func.count()).where(
                    PurchaseLine.purchase_id == purchase.id, col(PurchaseLine.received)
                )
            )
        ).one()
        await db.commit()
    return {"purchase_id": purchase.id, "code": purchase.code, "lines_received": received}


async def _send(plugin: Plugin, db: WriteSession, community_ref: str, row: OutboxEvent) -> None:
    """Send one event, having written the fields on its record's task."""
    name = events.EventName(row.name)
    payload = dict(row.payload)
    identity = events.IDENTITIES[name]
    if identity.kind in RECORDS:
        record_id = payload[identity.key[0]]
        link = (
            await db.exec(select(TaskLink).where(_link_column(identity.kind) == record_id))
        ).first()
        record = await db.get(RECORDS[identity.kind], record_id)
        if link is not None and record is not None:
            payload["task_id"] = link.task_id
            values = await fields(db, record)
            await _write_fields(plugin, community_ref, link.task_id, values, record.initiative_id)
    await plugin.api.emit_event(
        community_ref, plugin_manifest.event_id(name), payload, initiative_id=row.initiative_id
    )


def _wait(attempts: int) -> timedelta:
    return min(FIRST_WAIT * 2 ** min(attempts, 20), LONGEST_WAIT)


async def _send_next(plugin: Plugin, organization: organizations.Organization) -> bool:
    """Send the organization's oldest event, once it is due; answers whether
    the next may be sent now."""
    async with plugin.database.write_session(organization.id, actor=None) as db:
        locked = await db.execute(text(_OUTBOX_LOCK), {"organization": organization.id})
        if not locked.scalar_one():
            return False
        row = (await db.exec(select(OutboxEvent).order_by(col(OutboxEvent.id)).limit(1))).first()
        now = datetime.now(UTC)
        if row is None or row.next_attempt_at > now:
            return False
        try:
            await _send(plugin, db, organization.community_ref, row)
        except InitiativeRefused as refused:
            if refused.status_code in _LATER:
                return await _later(db, row, now)
            logger.warning(
                "Initiative refused Hoard's %s event (%s, %s); it is dropped",
                row.name,
                refused.status_code,
                refused.detail,
            )
        except InitiativeUnavailable:
            return await _later(db, row, now)
        await db.delete(row)
        await db.commit()
        return True


async def _later(db: WriteSession, row: OutboxEvent, now: datetime) -> bool:
    row.next_attempt_at = now + _wait(row.attempts)
    row.attempts += 1
    await db.commit()
    return False


async def deliver(plugin: Plugin, organization_id: int) -> None:
    """Send what the organization's outbox holds that is due, oldest first,
    stopping at the first that has to wait. A community that reaches nothing
    is sent nothing."""
    async with plugin.database.session() as db:
        organization = await organizations.get(db, organization_id)
    if organization is None or not organization.available:
        return
    while await _send_next(plugin, organization):
        pass


class _Courier:
    """The organizations whose commits recorded events, and the signal that one did."""

    def __init__(self) -> None:
        self.due: set[int] = set()
        self.woken = asyncio.Event()


#: The running :func:`deliver_forever`'s courier, if one runs.
_courier: _Courier | None = None
#: Where a session notes the organization it recorded events for.
_RECORDED = "hoard.recorded_events"


@event.listens_for(WriteSession.sync_session_class, "before_flush")
def _note_events(session: Session, _context: Any, _instances: Any) -> None:
    for row in session.new:
        if isinstance(row, OutboxEvent):
            session.info[_RECORDED] = row.organization_id


@event.listens_for(WriteSession.sync_session_class, "after_commit")
def _wake(session: Session) -> None:
    organization_id = session.info.pop(_RECORDED, None)
    if organization_id is not None and _courier is not None:
        _courier.due.add(organization_id)
        _courier.woken.set()


@event.listens_for(WriteSession.sync_session_class, "after_soft_rollback")
def _forget(session: Session, _transaction: Any) -> None:
    session.info.pop(_RECORDED, None)


async def _available(plugin: Plugin) -> set[int]:
    unavailable = [community_status.value for community_status in organizations.UNAVAILABLE]
    async with plugin.database.session() as db:
        found = await db.exec(
            select(organizations.Organization.id).where(
                col(organizations.Organization.community_status).not_in(unavailable)
            )
        )
        return set(found.all())


async def deliver_forever(plugin: Plugin, *, sweep_seconds: float = SWEEP_SECONDS) -> None:
    """Send every organization's due events, again and again, until cancelled:
    an organization's as soon as a commit records some there, and every
    organization's each ``sweep_seconds``."""
    global _courier
    courier = _courier = _Courier()
    swept: float | None = None
    try:
        while True:
            courier.woken.clear()
            due, courier.due = courier.due, set()
            if swept is None or time.monotonic() - swept >= sweep_seconds:
                swept = time.monotonic()
                try:
                    due |= await _available(plugin)
                except Exception:
                    logger.exception("could not list the organizations to send events for")
            for organization_id in sorted(due):
                try:
                    await deliver(plugin, organization_id)
                except Exception:
                    logger.exception("could not send organization %s's events", organization_id)
            with suppress(TimeoutError):
                async with asyncio.timeout(sweep_seconds):
                    await courier.woken.wait()
    finally:
        if _courier is courier:
            _courier = None
