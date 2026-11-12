"""A recall, written out: the lots it traces forward from (``hoard.trace``),
the lots made from them and the shipments they reached or may have, as the
files a regulator asks for and one a person reads.

* **The FDA's sortable spreadsheet** is CSV in the columns, and the order, of
  the Shipping tab of the FDA's template for the Food Traceability Rule. It
  lists the shipments to a business: shipping, in that rule, leaves out a
  sale to a consumer.
* **Canada's plain-text file** is one tab-separated file, as the Safe Food for
  Canadians Regulations ask: each lot with whom it came from or what went
  into it, then each lot provided to a business, with whom and when.
* **The PDF** lists everything, a sale to a consumer included, marking what
  only may have gone.

The parties (name, address and phone) are the community's contacts in
Initiative: a shipment's customer and carrier, a purchase's supplier, and the
contact a location names. Hoard keeps none of them.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import TextEmphasis
from fpdf.fonts import FontFace
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.events import item_name
from hoard.records import (
    Base,
    Location,
    Lot,
    Material,
    Order,
    ProductionRun,
    ProductionRunLine,
    Purchase,
    PurchaseLine,
    RunStatus,
    Shipment,
    Unit,
)
from hoard.trace import StepKind, Trace, TraceStep
from seneschal_core.initiative_api import InitiativeApi, InitiativeRefused

FONT = Path(__file__).parent / "fonts" / "DejaVuSans.ttf"
#: The characters a spreadsheet reads a cell starting with as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
#: What a location with no name and no contact is called: the main one.
MAIN_LOCATION = "Main location"
#: The unit a variation is counted in.
EACH = "each"

#: The Shipping tab of the FDA's sortable spreadsheet template, column by column.
FDA_COLUMNS = (
    "Traceability Lot Code",
    "Quantity",
    "Unit of Measure",
    "Product Description - Product Name",
    "Product Description - Brand Name (if applicable)",
    "Product Description - Commodity (if applicable)",
    "Product Description - Variety (if applicable)",
    "Product Description - Packaging Size",
    "Product Description - Packaging Style",
    "Immediate Subsequent Recipient - Business Name",
    "Immediate Subsequent Recipient - Phone Number",
    "Immediate Subsequent Recipient - Street Address or Geographic Coordinates",
    "Immediate Subsequent Recipient - City",
    "Immediate Subsequent Recipient - State or Region",
    "Immediate Subsequent Recipient - ZIP or Postal Code",
    "Immediate Subsequent Recipient - Country",
    "Location Shipped From - Business Name",
    "Location Shipped From - Phone Number",
    "Location Shipped From - Street Address or Geographic Coordinates",
    "Location Shipped From - City",
    "Location Shipped From - State or Region",
    "Location Shipped From - ZIP or Postal Code",
    "Location Shipped From - Country",
    "Shipping Date",
    "Traceability Lot Code Source - Business Name",
    "Traceability Lot Code Source - Phone Number",
    "Traceability Lot Code Source - Street Address or Geographic Coordinates",
    "Traceability Lot Code Source - City",
    "Traceability Lot Code Source - State/Region",
    "Traceability Lot Code Source - ZIP/Postal Code",
    "Traceability Lot Code Source - Country",
    "Traceability Lot Code Source Reference",
    "Reference Document Type",
    "Reference Document Number",
    "Reference Document Type2",
    "Reference Document Number2",
)

CANADA_COLUMNS = (
    "Common name",
    "Lot code",
    "Manufactured by",
    "Manufactured by: address",
    "Received from",
    "Received from: address",
    "Received on",
    "Provided to",
    "Provided to: address",
    "Provided on",
    "Ingredients",
)


@dataclass(frozen=True)
class Party:
    """Someone a recall names: a contact's name, first address and first phone."""

    name: str = ""
    address: str = ""
    phone: str = ""


def _detail(contact: dict[str, Any], key: str) -> str:
    details = contact.get(key)
    if isinstance(details, list) and details and isinstance(details[0], dict):
        return str(details[0].get("value") or "")
    return ""


def party(contact: object) -> Party | None:
    """An Initiative contact as a recall names it; None for anything else."""
    if not isinstance(contact, dict):
        return None
    return Party(
        name=str(contact.get("name") or ""),
        address=_detail(contact, "addresses"),
        phone=_detail(contact, "phones"),
    )


async def parties(
    api: InitiativeApi, community_ref: str, contact_ids: Iterable[int]
) -> dict[int, Party]:
    """Each of ``contact_ids`` the community's install may read, as a party.
    A contact Initiative refuses is left out; ``InitiativeUnavailable`` is
    raised when Initiative does not answer."""
    found: dict[int, Party] = {}
    for contact_id in sorted(set(contact_ids)):
        try:
            answer = await api.call(community_ref, "GET", f"/contacts/{contact_id}")
        except InitiativeRefused:
            continue
        if (named := party(answer)) is not None:
            found[contact_id] = named
    return found


def lot_of(step: TraceStep) -> int | None:
    """The lot a step moved, or, for a draw that named none, the lot that held
    stock when it was made."""
    return step.lot_id if step.lot_id is not None else step.from_lot_id


@dataclass
class Recall:
    """The traces of a recall, with the records its files name."""

    traces: list[Trace]
    lots: dict[int, Lot] = field(default_factory=dict)
    #: Each item's name and the unit it is counted in.
    items: dict[int, tuple[str, str]] = field(default_factory=dict)
    locations: dict[int, Location] = field(default_factory=dict)
    #: The purchase that received each bought lot, by lot.
    purchases: dict[int, Purchase] = field(default_factory=dict)
    #: The items consumed by the run that made each lot made, by lot.
    ingredients: dict[int, list[int]] = field(default_factory=dict)
    shipments: dict[int, tuple[Shipment, Order]] = field(default_factory=dict)

    def steps(self, kind: StepKind) -> list[TraceStep]:
        """Every step of a kind, once each, in the order the traces reach them."""
        seen: set[tuple[Any, ...]] = set()
        found: list[TraceStep] = []
        for trace in self.traces:
            for step in trace.steps:
                key = (step.record_id, lot_of(step), step.item_id, step.quantity, step.made_lot_id)
                if step.kind is kind and key not in seen:
                    seen.add(key)
                    found.append(step)
        return found

    def made_lot_ids(self) -> list[int]:
        """The lots made from the recalled ones, in the order the traces reach them."""
        return list(
            dict.fromkeys(
                step.made_lot_id
                for step in self.steps(StepKind.RUN)
                if step.made_lot_id is not None
            )
        )

    def contact_ids(self) -> set[int]:
        """Every contact its files name."""
        ids = {
            location.contact_id
            for location in self.locations.values()
            if location.contact_id is not None
        }
        ids |= {
            purchase.supplier_contact_id
            for purchase in self.purchases.values()
            if purchase.supplier_contact_id is not None
        }
        for shipment, order in self.shipments.values():
            ids |= {
                contact_id
                for contact_id in (order.customer_contact_id, shipment.carrier_contact_id)
                if contact_id is not None
            }
        return ids


async def gather(db: AsyncSession, traces: list[Trace]) -> Recall:
    """The records a recall's files name, read once."""
    recall = Recall(traces=traces)
    lot_ids = {trace.lot_id for trace in traces}
    for trace in traces:
        lot_ids |= trace.lot_ids()
        lot_ids |= {lot_id for step in trace.steps if (lot_id := lot_of(step)) is not None}
    lots = await db.exec(select(Lot).where(col(Lot.id).in_(lot_ids)))
    recall.lots = {lot.id: lot for lot in lots.all()}
    bought = await db.exec(
        select(Lot.id, Purchase)
        .join(PurchaseLine, col(PurchaseLine.id) == Lot.purchase_line_id)
        .join(Purchase, col(Purchase.id) == PurchaseLine.purchase_id)
        .where(col(Lot.id).in_(lot_ids))
    )
    recall.purchases = dict(bought.all())
    consumed = await db.exec(
        select(ProductionRun.output_lot_id, ProductionRunLine.item_id)
        .join(ProductionRun, col(ProductionRun.id) == ProductionRunLine.run_id)
        .where(
            col(ProductionRun.output_lot_id).in_(lot_ids),
            ProductionRun.status == RunStatus.COMPLETED,
        )
        .order_by(col(ProductionRunLine.id))
    )
    item_ids = {lot.item_id for lot in recall.lots.values()}
    for lot_id, item_id in consumed.all():
        if lot_id is not None:
            recall.ingredients.setdefault(lot_id, []).append(item_id)
            item_ids.add(item_id)
    shipment_ids = {step.record_id for step in recall.steps(StepKind.SHIPMENT)}
    item_ids |= {step.item_id for trace in traces for step in trace.steps}
    shipped = await db.exec(
        select(Shipment, Order)
        .join(Order, col(Order.id) == Shipment.order_id)
        .where(col(Shipment.id).in_(shipment_ids))
    )
    recall.shipments = {shipment.id: (shipment, order) for shipment, order in shipped.all()}
    location_ids = {lot.location_id for lot in recall.lots.values()}
    location_ids |= {shipment.location_id for shipment, _ in recall.shipments.values()}
    locations = await db.exec(select(Location).where(col(Location.id).in_(location_ids)))
    recall.locations = {location.id: location for location in locations.all()}
    unit_of: dict[int, str] = {}
    for kind in (Material, Base):
        units = await db.exec(
            select(kind.id, Unit.name)
            .join(Unit, col(Unit.id) == kind.tracking_unit_id)
            .where(col(kind.id).in_(item_ids))
        )
        unit_of.update(units.all())
    for item_id in sorted(item_ids):
        recall.items[item_id] = (await item_name(db, item_id), unit_of.get(item_id, EACH))
    return recall


def _quantity(quantity: Decimal) -> str:
    return format(quantity.normalize(), "f")


def _cell(value: str) -> str:
    """Text as a spreadsheet cell: one starting as a formula would starts with
    an apostrophe, so it opens as the text it is."""
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


def _line(value: str) -> str:
    """Text on one line: an address keeps its parts, separated by commas."""
    return ", ".join(part.strip() for part in value.splitlines() if part.strip())


class _Names:
    """The parties of a recall, by what names them."""

    def __init__(self, recall: Recall, contacts: dict[int, Party]) -> None:
        self.recall = recall
        self.contacts = contacts

    def contact(self, contact_id: int | None) -> Party:
        return self.contacts.get(contact_id, Party()) if contact_id is not None else Party()

    def place(self, location_id: int) -> Party:
        """A location, as its contact names it, or by its own name."""
        location = self.recall.locations.get(location_id)
        if location is None:
            return Party()
        if location.contact_id in self.contacts:
            return self.contacts[location.contact_id]
        return Party(name=location.name or (MAIN_LOCATION if location.main else ""))

    def source(self, lot: Lot) -> Party:
        """Where a lot's code was given: its supplier, for a bought lot, or
        the location that made it."""
        purchase = self.recall.purchases.get(lot.id)
        if purchase is not None:
            return self.contact(purchase.supplier_contact_id)
        return self.place(lot.location_id)


def _party_columns(party: Party) -> list[str]:
    """A party as the template's location description: the address is one
    block of text, so it fills the street column and the rest stay empty."""
    return [party.name, party.phone, _line(party.address), "", "", "", ""]


def fda_csv(recall: Recall, contacts: dict[int, Party]) -> str:
    """The FDA's sortable spreadsheet: each shipment to a business, recorded
    or assumed, in the Shipping tab's columns."""
    names = _Names(recall, contacts)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(FDA_COLUMNS)
    for step in recall.steps(StepKind.SHIPMENT):
        shipment, order = recall.shipments[step.record_id]
        lot = recall.lots.get(lot_of(step) or 0)
        if order.customer_contact_id is None or lot is None:
            continue
        name, unit = recall.items[step.item_id]
        row = [
            lot.number,
            _quantity(step.quantity),
            unit,
            name,
            "",
            "",
            "",
            "",
            "",
            *_party_columns(names.contact(order.customer_contact_id)),
            *_party_columns(names.place(shipment.location_id)),
            step.dated_on.strftime("%m/%d/%Y"),
            *_party_columns(names.source(lot)),
            "",
            "Order",
            order.code,
            "Shipment reference" if shipment.reference else "",
            shipment.reference or "",
        ]
        writer.writerow([_cell(value) for value in row])
    return out.getvalue()


def _text(value: str) -> str:
    """A value in a tab-separated file: on one line, with no tab."""
    return _line(value).replace("\t", " ")


def canada_text(recall: Recall, contacts: dict[int, Party]) -> str:
    """Canada's single plain-text file, tab-separated: each lot with whom it
    came from or what went into it, then each lot provided to a business."""
    names = _Names(recall, contacts)
    rows: list[list[str]] = []
    lot_ids = [*dict.fromkeys(trace.lot_id for trace in recall.traces), *recall.made_lot_ids()]
    for lot_id in dict.fromkeys(lot_ids):
        lot = recall.lots[lot_id]
        purchase = recall.purchases.get(lot_id)
        made_by = Party() if purchase is not None else names.place(lot.location_id)
        supplier = names.contact(purchase.supplier_contact_id) if purchase else Party()
        ingredients = [recall.items[item_id][0] for item_id in recall.ingredients.get(lot_id, [])]
        rows.append(
            [
                recall.items[lot.item_id][0],
                lot.number,
                made_by.name,
                made_by.address,
                supplier.name,
                supplier.address,
                purchase.dated_on.isoformat() if purchase else "",
                "",
                "",
                "",
                "; ".join(dict.fromkeys(ingredients)),
            ]
        )
    for step in recall.steps(StepKind.SHIPMENT):
        _, order = recall.shipments[step.record_id]
        lot = recall.lots.get(lot_of(step) or 0)
        if order.customer_contact_id is None or lot is None:
            continue
        made_by = Party() if lot.id in recall.purchases else names.place(lot.location_id)
        recipient = names.contact(order.customer_contact_id)
        rows.append(
            [
                recall.items[step.item_id][0],
                lot.number,
                made_by.name,
                made_by.address,
                "",
                "",
                "",
                recipient.name,
                recipient.address,
                step.dated_on.isoformat(),
                "",
            ]
        )
    lines: list[str] = ["\t".join(CANADA_COLUMNS)]
    lines += ["\t".join(_text(value) for value in row) for row in rows]
    return "\r\n".join(lines) + "\r\n"


def _how(step: TraceStep) -> str:
    """Whether someone recorded a step, or it only may have happened."""
    return "May have" if step.assumed else "Recorded"


class _Pdf(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="landscape", format="A4")
        self.add_font("DejaVu", fname=str(FONT))
        self.set_font("DejaVu", size=9)
        self.set_auto_page_break(auto=True, margin=12)
        self.add_page()

    def heading(self, text: str, size: int = 13) -> None:
        self.ln(3)
        self.set_font_size(size)
        self.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")
        self.set_font_size(9)
        self.ln(1)

    def rows(self, columns: tuple[str, ...], rows: list[list[str]], none: str) -> None:
        if not rows:
            self.multi_cell(0, 5, none, new_x="LMARGIN", new_y="NEXT")
            return
        headings = FontFace(emphasis=TextEmphasis.NONE, fill_color=(235, 235, 235))
        with self.table(headings_style=headings, text_align="LEFT", line_height=4.5) as table:
            table.row(columns)
            for row in rows:
                table.row(row)


def pdf(recall: Recall, contacts: dict[int, Party], prepared_on: date) -> bytes:
    """A recall to read: the lots recalled, the lots made from them, and every
    shipment they reached or may have, a sale to a consumer included."""
    names = _Names(recall, contacts)
    document = _Pdf()
    document.heading("Recall", 18)
    document.multi_cell(0, 5, f"Prepared {prepared_on.isoformat()}", new_x="LMARGIN", new_y="NEXT")

    def lot_number(lot_id: int | None) -> str:
        lot = recall.lots.get(lot_id or 0)
        return lot.number if lot is not None else ""

    recalled: list[list[str]] = []
    for trace in recall.traces:
        lot = recall.lots[trace.lot_id]
        source = names.source(lot)
        recalled.append(
            [
                lot.number,
                recall.items[lot.item_id][0],
                lot.dated_on.isoformat(),
                names.place(lot.location_id).name,
                ", ".join(filter(None, [source.name, _line(source.address), source.phone])),
            ]
        )
    document.heading("Lots recalled")
    document.rows(("Lot", "Item", "Dated", "Location", "Source"), recalled, "None")

    made: list[list[str]] = []
    for step in recall.steps(StepKind.RUN):
        made_lot = recall.lots.get(step.made_lot_id or 0)
        if made_lot is None:
            continue
        made.append(
            [
                made_lot.number,
                recall.items[made_lot.item_id][0],
                step.dated_on.isoformat(),
                lot_number(lot_of(step)),
                f"{_quantity(step.quantity)} {recall.items[step.item_id][1]}",
                _how(step),
            ]
        )
    document.heading("Lots made from them")
    document.rows(("Lot", "Item", "Made on", "From lot", "Used", ""), made, "None")

    shipped: list[list[str]] = []
    for step in recall.steps(StepKind.SHIPMENT):
        shipment, order = recall.shipments[step.record_id]
        recipient = names.contact(order.customer_contact_id)
        carrier = names.contact(shipment.carrier_contact_id)
        name, unit = recall.items[step.item_id]
        shipped.append(
            [
                step.dated_on.isoformat(),
                order.code if order.external_number is None else order.external_number,
                recipient.name if order.customer_contact_id is not None else "Consumer",
                _line(recipient.address or shipment.delivery_address or ""),
                recipient.phone,
                carrier.name,
                name,
                lot_number(lot_of(step)),
                f"{_quantity(step.quantity)} {unit}",
                _how(step),
            ]
        )
    document.heading("Shipments")
    document.rows(
        (
            "Shipped on",
            "Order",
            "Recipient",
            "Address",
            "Phone",
            "Carrier",
            "Item",
            "Lot",
            "Quantity",
            "",
        ),
        shipped,
        "None",
    )
    return bytes(document.output())
