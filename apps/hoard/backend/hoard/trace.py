"""Tracing a lot: back to the lots and suppliers that went into it, and
forward to what was made from it and where it went.

**Back**, a production run's lines lead to the lots it consumed, a bought lot
to its purchase line and supplier, at every level. **Forward** is two lists:
what went out with the lot as someone recorded it (the runs and shipments
that name it, not assumed, at every level), and what may have (the lines that
assume it, and the draws naming no lot of its item at its location while it
held stock). A **recall** traces forward from a lot, or from every lot of an
item dated within a range.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlmodel.ext.asyncio.session import AsyncSession


class StepKind(StrEnum):
    """What a step of a trace is."""

    #: A production run that consumed one lot and made another.
    RUN = "run"
    #: A shipment that sent a lot out.
    SHIPMENT = "shipment"
    #: A purchase that received a lot.
    PURCHASE = "purchase"


@dataclass(frozen=True)
class TraceStep:
    """One movement a trace follows: a lot consumed by a run, shipped, or
    received."""

    kind: StepKind
    #: The run, shipment or purchase.
    record_id: int
    #: The lot that moved; None for a draw that named none.
    lot_id: int | None
    #: The lot a run made of it.
    made_lot_id: int | None
    item_id: int
    quantity: Decimal
    dated_on: date
    #: The other party: a shipment's customer, a purchase's supplier, as an
    #: Initiative contact's id.
    contact_id: int | None
    #: Whether the lot only may have moved: an assumed or unnamed draw.
    assumed: bool
    #: How many steps from the traced lot: 1 for its own.
    level: int


@dataclass
class Trace:
    """Every step a trace from one lot reaches."""

    lot_id: int
    steps: list[TraceStep] = field(default_factory=list)

    def lot_ids(self, *, assumed: bool = True) -> set[int]:
        """The lots the trace reaches beside its own: made from it, forward,
        or gone into it, back; only those recorded unless ``assumed``."""
        return {
            lot_id
            for step in self.steps
            if assumed or not step.assumed
            for lot_id in (step.made_lot_id, step.lot_id)
            if lot_id is not None and lot_id != self.lot_id
        }


async def forward(db: AsyncSession, lot_id: int) -> Trace:
    """Where a lot went: the runs and shipments that drew on it, recorded
    and assumed, and on from each lot those runs made, at every level."""
    raise NotImplementedError


async def back(db: AsyncSession, lot_id: int) -> Trace:
    """What went into a lot: the lots its run consumed, at every level, and
    the purchases that received each bought lot."""
    raise NotImplementedError


async def recall(
    db: AsyncSession,
    *,
    lot_id: int | None = None,
    item_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[Trace]:
    """The forward traces of a lot, or of each lot of an item dated from
    ``since`` to ``until``."""
    raise NotImplementedError
