"""What a scan names in Hoard: the item and the lot a label is for.

A page sends what a scan, or a person, produced (``POST /scans``), and Hoard
answers what it read (``seneschal_core.gs1``) and what it holds of it:

- **the items:** by the label's GTIN; or, for a plain code, by SKU. A number
  that reads as a GTIN but no record holds is looked up as a SKU too, since a
  business's own numeric SKU can look like one;
- **the lots:** those of the items whose number is the label's lot, or, for a
  plain code, any lot whose number it is: at the location the page names,
  when it names one, each with its balance, expiry and whether it is on hold;
- **the problems** the page should show: ``unknown_gtin``,
  ``item_mismatch`` (the label is another item's than the page's),
  ``lot_not_found``, ``lot_on_hold``, ``expiry_differs`` (the label's expiry
  isn't the lot's), and the reader's own (``ambiguous``, ``check_digit``…).

A number is matched exactly, then without regard to case and surrounding
space, which the answer marks ``loose``. It writes nothing; the page decides
what to do with the answer.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date
from decimal import Decimal
from enum import StrEnum

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.api.adjustments import describe_items
from hoard.holds import held_lots
from hoard.records import Base, ItemKind, Lot, Material, Variation
from seneschal_core.api import OrganizationSessionDep
from seneschal_core.gs1 import Gs1Scan, read_scan

router = APIRouter(prefix="/scans", tags=["scans"])

#: The most items or lots one scan answers with.
MOST = 20


class ScanProblem(StrEnum):
    UNKNOWN_GTIN = "unknown_gtin"
    ITEM_MISMATCH = "item_mismatch"
    LOT_NOT_FOUND = "lot_not_found"
    LOT_ON_HOLD = "lot_on_hold"
    EXPIRY_DIFFERS = "expiry_differs"


class ScanAsk(BaseModel):
    """A scan, and where the page that took it stands."""

    #: What the scanner, the camera or the person produced.
    text: str = Field(min_length=1, max_length=2048)
    #: What the scanner knew of the symbol: true for GS1, false for a plain
    #: barcode, null when it didn't say.
    gs1: bool | None = None
    #: The location the page works at: lots elsewhere are left out.
    location_id: int | None = None
    #: The item the page expects, such as a shipment line's.
    item_id: int | None = None


class ScanItem(BaseModel):
    id: int
    kind: ItemKind
    #: Its name; a variation's is its product's, with its attributes.
    name: str
    attributes: dict[str, str]
    archived: bool
    #: Its page.
    path: str


class ScanLot(BaseModel):
    id: int
    item_id: int
    location_id: int
    number: str
    expires_on: date | None
    balance: Decimal
    on_hold: bool
    #: Its number matched only without regard to case and space.
    loose: bool
    #: Its page.
    path: str


class ScanRead(BaseModel):
    """What a scan says, and what Hoard holds of it."""

    #: ``gs1`` for a GS1 label or a retail barcode, ``plain`` for a code.
    kind: str
    #: The text as read: a plain code, or the scan as it came.
    text: str
    gtin: str | None
    lot: str | None
    serial: str | None
    sscc: str | None
    expires_on: date | None
    best_before: date | None
    produced_on: date | None
    count: int | None
    items: list[ScanItem]
    lots: list[ScanLot]
    #: The reader's (``ambiguous``, ``check_digit``, ``date``…) and Hoard's.
    problems: list[str]


async def _by_gtin(db: AsyncSession, gtin: str) -> list[int]:
    found: list[int] = []
    for model in (Material, Base, Variation):
        found += (await db.exec(select(model.id).where(col(model.gtin) == gtin))).all()
    return found


async def _by_sku(db: AsyncSession, code: str) -> list[int]:
    wanted = code.strip().lower()
    found: list[int] = []
    for model in (Material, Base, Variation):
        found += (
            await db.exec(
                select(model.id)
                .where(func.lower(func.trim(col(model.sku))) == wanted)
                .order_by(col(model.id))
                .limit(MOST)
            )
        ).all()
    return found


async def _lots(
    db: AsyncSession,
    number: str,
    *,
    item_ids: Collection[int] | None,
    location_id: int | None,
) -> list[tuple[Lot, bool]]:
    """The lots whose number is ``number``, exactly, else without regard to
    case and space; of ``item_ids`` when given. Each with whether it matched
    only loosely."""

    def lots_where(exact: bool):
        query = select(Lot)
        if exact:
            query = query.where(Lot.number == number)
        else:
            query = query.where(func.lower(func.trim(col(Lot.number))) == number.strip().lower())
        if item_ids is not None:
            query = query.where(col(Lot.item_id).in_(item_ids))
        if location_id is not None:
            query = query.where(Lot.location_id == location_id)
        return query.order_by(col(Lot.dated_on), col(Lot.id)).limit(MOST)

    exact = (await db.exec(lots_where(exact=True))).all()
    if exact:
        return [(lot, False) for lot in exact]
    return [(lot, True) for lot in (await db.exec(lots_where(exact=False))).all()]


async def _paths(db: AsyncSession, item_ids: Sequence[int]) -> dict[int, str]:
    paths = {
        **{
            id_: f"/stock/materials/{id_}"
            for id_ in (
                await db.exec(select(Material.id).where(col(Material.id).in_(item_ids)))
            ).all()
        },
        **{
            id_: f"/stock/bases/{id_}"
            for id_ in (await db.exec(select(Base.id).where(col(Base.id).in_(item_ids)))).all()
        },
    }
    variations = await db.exec(
        select(Variation.id, Variation.product_id).where(col(Variation.id).in_(item_ids))
    )
    paths |= {id_: f"/stock/products/{product_id}" for id_, product_id in variations.all()}
    return paths


@router.post("")
async def scan_code(body: ScanAsk, db: OrganizationSessionDep) -> ScanRead:
    """What a scan says, and the items and lots of Hoard's it names."""
    return await resolve(db, body)


async def resolve(db: AsyncSession, body: ScanAsk) -> ScanRead:
    """What a scan says, and the items and lots of Hoard's it names: the
    route's answer, for the pages' own scans (a shipment's) to start from."""
    scan = read_scan(body.text, gs1=body.gs1)
    problems: list[str] = []
    item_ids: list[int] = []
    if isinstance(scan, Gs1Scan):
        problems += scan.problems
        if scan.gtin is not None:
            item_ids = await _by_gtin(db, scan.gtin)
            if not item_ids and scan.raw:
                # A business's own numeric SKU can read as a GTIN.
                item_ids = await _by_sku(db, scan.raw)
            if not item_ids:
                problems.append(ScanProblem.UNKNOWN_GTIN)
        lot_number = scan.lot
        lots = (
            await _lots(db, lot_number, item_ids=item_ids, location_id=body.location_id)
            if lot_number and item_ids
            else []
        )
        if lot_number and item_ids and not lots:
            problems.append(ScanProblem.LOT_NOT_FOUND)
        expires_on = scan.expires_on
    else:
        item_ids = await _by_sku(db, scan.text)
        lots = await _lots(db, scan.text, item_ids=None, location_id=body.location_id)
        expires_on = None
    if body.item_id is not None and item_ids and body.item_id not in item_ids:
        problems.append(ScanProblem.ITEM_MISMATCH)

    lot_ids = [lot.id for lot, _ in lots]
    held: set[int] = set()
    if lot_ids:
        on_hold = select(Lot.id).where(col(Lot.id).in_(lot_ids), col(Lot.id).in_(held_lots()))
        held = set((await db.exec(on_hold)).all())
    if held:
        problems.append(ScanProblem.LOT_ON_HOLD)
    if expires_on is not None and any(
        lot.expires_on is not None and lot.expires_on != expires_on for lot, _ in lots
    ):
        problems.append(ScanProblem.EXPIRY_DIFFERS)

    every_item = list(dict.fromkeys([*item_ids, *(lot.item_id for lot, _ in lots)]))
    described = await describe_items(db, every_item)
    paths = await _paths(db, every_item)
    label = scan if isinstance(scan, Gs1Scan) else None
    return ScanRead(
        kind="gs1" if label else "plain",
        text=scan.raw if isinstance(scan, Gs1Scan) else scan.text,
        gtin=label.gtin if label else None,
        lot=label.lot if label else None,
        serial=label.serial if label else None,
        sscc=label.sscc if label else None,
        expires_on=expires_on,
        best_before=label.best_before if label else None,
        produced_on=label.produced_on if label else None,
        count=label.count if label else None,
        items=[
            ScanItem(
                id=id_,
                kind=described[id_].kind,
                name=described[id_].name,
                attributes=described[id_].attributes,
                archived=described[id_].archived,
                path=paths[id_],
            )
            for id_ in item_ids[:MOST]
            if id_ in described and id_ in paths
        ],
        lots=[
            ScanLot(
                id=lot.id,
                item_id=lot.item_id,
                location_id=lot.location_id,
                number=lot.number,
                expires_on=lot.expires_on,
                balance=lot.balance,
                on_hold=lot.id in held,
                loose=loose,
                path=f"/stock/lots/{lot.id}",
            )
            for lot, loose in lots
        ],
        problems=list(dict.fromkeys(problems)),
    )
