"""A lot's trace, back and forward, and recalls (``hoard.trace``,
``hoard.recall``), with their exports.

A step names the other party, a shipment's customer or a purchase's supplier,
by the community's contact, with the contact's name where Initiative lets the
install read it. A recall's files need every party, so they wait for
Initiative; a trace on a page shows what it has without the names.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel
from sqlmodel import col, select

from hoard import recall as recalls
from hoard import trace
from hoard.api.adjustments import AdjustmentItem, Responses, describe_items
from hoard.api.deps import HoardError
from hoard.api.settings import found
from hoard.records import Item, Lot, Order, ProductionRun, Purchase, Shipment
from hoard.trace import StepKind, Trace, TraceStep
from seneschal_core.api import (
    ErrorCode,
    OrganizationDep,
    OrganizationSessionDep,
    Plugin,
    PluginDep,
    refuse,
)
from seneschal_core.db import OrganizationSession
from seneschal_core.initiative_api import InitiativeUnavailable
from seneschal_core.organizations import Organization

router = APIRouter(prefix="/trace", tags=["trace"])

_READ: Responses = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_RECALL: Responses = {**_READ, 422: {"description": "REQUEST_INVALID"}}


class TraceLotRef(BaseModel):
    """A lot a step names."""

    id: int
    number: str


class TracePartyRead(BaseModel):
    """The other party to a step: a community contact."""

    contact_id: int
    #: Its name, once Initiative lets the install read it.
    name: str | None


class TraceStepRead(BaseModel):
    """One movement a trace follows."""

    kind: StepKind
    #: The run, shipment or purchase.
    record_id: int
    #: The run's or the purchase's code, or the code of the order a shipment is of.
    code: str
    #: The order a shipment is of.
    order_id: int | None
    #: The lot it moved; null for a draw that named none.
    lot: TraceLotRef | None
    #: The lot a run made.
    made_lot: TraceLotRef | None
    #: The lot of the trace it goes on from: the lot it moved, or, for a draw
    #: that named none, the lot that held stock when it was made.
    from_lot: TraceLotRef | None
    item: AdjustmentItem
    quantity: Decimal
    dated_on: date
    #: A shipment's customer or a purchase's supplier; null for a consumer.
    party: TracePartyRead | None
    #: Whether it only may have happened: an assumed lot or a draw naming none.
    assumed: bool
    #: How many steps from the traced lot: 1 for its own.
    level: int


class TraceLotRead(BaseModel):
    """A lot's trace: back to what went into it, forward to where it went."""

    back: list[TraceStepRead]
    forward: list[TraceStepRead]


class TraceRead(BaseModel):
    """A forward trace from one recalled lot."""

    lot: TraceLotRef
    item: AdjustmentItem
    dated_on: date
    steps: list[TraceStepRead]


class TraceRecallRead(BaseModel):
    """A recall: the forward trace of each lot it starts from."""

    traces: list[TraceRead]


class TraceRecallFormat(StrEnum):
    """The files a recall downloads as."""

    #: The FDA's sortable spreadsheet, as CSV.
    FDA = "fda"
    #: Canada's single plain-text file.
    CANADA = "canada"
    PDF = "pdf"


async def _read_steps(
    db: OrganizationSession, organization: Organization, plugin: Plugin, traces: list[Trace]
) -> list[list[TraceStepRead]]:
    """Each trace's steps, as pages read them, naming the parties Initiative
    names, or none while it does not answer."""
    steps = [step for found_trace in traces for step in found_trace.steps]
    lot_ids = {
        lot_id
        for step in steps
        for lot_id in (step.lot_id, step.made_lot_id, step.from_lot_id)
        if lot_id is not None
    }
    numbers = dict(
        (await db.exec(select(Lot.id, Lot.number).where(col(Lot.id).in_(lot_ids)))).all()
    )
    items = await describe_items(db, {step.item_id for step in steps})
    by_kind = {kind: {s.record_id for s in steps if s.kind is kind} for kind in StepKind}
    runs = await db.exec(
        select(ProductionRun.id, ProductionRun.code).where(
            col(ProductionRun.id).in_(by_kind[StepKind.RUN])
        )
    )
    purchases = await db.exec(
        select(Purchase.id, Purchase.code).where(col(Purchase.id).in_(by_kind[StepKind.PURCHASE]))
    )
    shipments = await db.exec(
        select(Shipment.id, Order.id, Order.code)
        .join(Order, col(Order.id) == Shipment.order_id)
        .where(col(Shipment.id).in_(by_kind[StepKind.SHIPMENT]))
    )
    codes: dict[tuple[StepKind, int], tuple[str, int | None]] = {
        **{(StepKind.RUN, id_): (code, None) for id_, code in runs.all()},
        **{(StepKind.PURCHASE, id_): (code, None) for id_, code in purchases.all()},
        **{(StepKind.SHIPMENT, id_): (code, order) for id_, order, code in shipments.all()},
    }
    contacts = {step.contact_id for step in steps if step.contact_id is not None}
    names: dict[int, recalls.Party] = {}
    if contacts:
        try:
            names = await recalls.parties(plugin.api, organization.community_ref, contacts)
        except InitiativeUnavailable:
            pass

    def lot(lot_id: int | None) -> TraceLotRef | None:
        if lot_id is None or lot_id not in numbers:
            return None
        return TraceLotRef(id=lot_id, number=numbers[lot_id])

    def read(step: TraceStep) -> TraceStepRead:
        code, order_id = codes[(step.kind, step.record_id)]
        party = None
        if step.contact_id is not None:
            named = names.get(step.contact_id)
            party = TracePartyRead(
                contact_id=step.contact_id, name=named.name if named is not None else None
            )
        return TraceStepRead(
            kind=step.kind,
            record_id=step.record_id,
            code=code,
            order_id=order_id,
            lot=lot(step.lot_id),
            made_lot=lot(step.made_lot_id),
            from_lot=lot(step.from_lot_id),
            item=items[step.item_id],
            quantity=step.quantity,
            dated_on=step.dated_on,
            party=party,
            assumed=step.assumed,
            level=step.level,
        )

    return [[read(step) for step in found_trace.steps] for found_trace in traces]


@router.get("/lots/{lot_id}", responses=_READ)
async def read_lot_trace(
    lot_id: int, db: OrganizationSessionDep, organization: OrganizationDep, plugin: PluginDep
) -> TraceLotRead:
    """A lot's trace: back to the lots and suppliers that went into it, and
    forward to the lots made from it and the shipments it reached or may have."""
    await found(db, Lot, lot_id)
    back, forward = await trace.back(db, lot_id), await trace.forward(db, lot_id)
    back_steps, forward_steps = await _read_steps(db, organization, plugin, [back, forward])
    return TraceLotRead(back=back_steps, forward=forward_steps)


async def _recall(
    db: OrganizationSession,
    lot_id: int | None,
    item_id: int | None,
    since: date | None,
    until: date | None,
) -> list[Trace]:
    """A recall from a lot, or from an item's lots dated in a range."""
    if lot_id is not None:
        await found(db, Lot, lot_id)
    elif item_id is not None:
        await found(db, Item, item_id)
    else:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
    if since is not None and until is not None and since > until:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.REQUEST_INVALID)
    return await trace.recall(db, lot_id=lot_id, item_id=item_id, since=since, until=until)


@router.get("/recall", responses=_RECALL)
async def read_recall(
    db: OrganizationSessionDep,
    organization: OrganizationDep,
    plugin: PluginDep,
    lot_id: int | None = None,
    item_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> TraceRecallRead:
    """A recall from a lot, or from each lot of an item dated from ``since``
    to ``until``: every lot made from it, and every shipment it reached or
    may have reached."""
    traces = await _recall(db, lot_id, item_id, since, until)
    recalled = await db.exec(select(Lot).where(col(Lot.id).in_([t.lot_id for t in traces])))
    lots = {lot.id: lot for lot in recalled.all()}
    items = await describe_items(db, {lot.item_id for lot in lots.values()})
    steps = await _read_steps(db, organization, plugin, traces)
    return TraceRecallRead(
        traces=[
            TraceRead(
                lot=TraceLotRef(id=lot.id, number=lot.number),
                item=items[lot.item_id],
                dated_on=lot.dated_on,
                steps=read,
            )
            for lot, read in zip(
                (lots[found_trace.lot_id] for found_trace in traces), steps, strict=True
            )
        ]
    )


#: Each recall file's type and name.
_FILES = {
    TraceRecallFormat.FDA: ("text/csv; charset=utf-8", "recall-fda.csv"),
    TraceRecallFormat.CANADA: ("text/plain; charset=utf-8", "recall-canada.txt"),
    TraceRecallFormat.PDF: ("application/pdf", "recall.pdf"),
}


@router.get(
    "/recall/export",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}, "text/plain": {}, "application/pdf": {}}},
        **_RECALL,
        503: {"description": "INITIATIVE_UNAVAILABLE"},
    },
)
async def export_recall(
    db: OrganizationSessionDep,
    organization: OrganizationDep,
    plugin: PluginDep,
    file_format: Annotated[TraceRecallFormat, Query(alias="format")],
    lot_id: int | None = None,
    item_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> Response:
    """A recall as a file to download: the FDA's sortable spreadsheet,
    Canada's plain-text file, or a PDF to read. Every party is named from the
    community's contacts, so it waits for Initiative."""
    traces = await _recall(db, lot_id, item_id, since, until)
    gathered = await recalls.gather(db, traces)
    try:
        contacts = await recalls.parties(
            plugin.api, organization.community_ref, gathered.contact_ids()
        )
    except InitiativeUnavailable:
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE
        ) from None
    content: str | bytes
    if file_format is TraceRecallFormat.FDA:
        content = recalls.fda_csv(gathered, contacts)
    elif file_format is TraceRecallFormat.CANADA:
        content = recalls.canada_text(gathered, contacts)
    else:
        content = recalls.pdf(gathered, contacts, date.today())
    media_type, name = _FILES[file_format]
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
