"""A recall written out: the parties from the community's contacts, the FDA's
sortable spreadsheet, Canada's plain-text file and the PDF."""

from __future__ import annotations

import csv
import io
from datetime import date

import httpx2

from hoard import recall, trace
from hoard.recall import CANADA_COLUMNS, FDA_COLUMNS, Party
from hoard.records import Location
from hoard.trace_test import chai, ship  # noqa: F401  (fixtures)
from seneschal_core.db import WriteSession
from seneschal_core.testing import InitiativeStandIn

CUSTOMER = Party("Corner Café", "12 High St\nLeeds LS1 4AB", "0113 496 0000")
SUPPLIER = Party("=Spice Co", "", "")
CONTACTS = {9: CUSTOMER, 7: SUPPLIER}


def _contact(id_: int, name: str, **details: list[dict[str, str]]) -> dict:
    return {"id": id_, "name": name, "is_organization": True, **details}


async def test_parties_are_the_contacts_initiative_lets_the_install_read(
    initiative_stand_in: InitiativeStandIn, community_ref: str
):
    initiative_stand_in.routes[("GET", "/contacts/9")] = lambda _r: httpx2.Response(
        200,
        json=_contact(
            9,
            "Corner Café",
            addresses=[{"label": "Shop", "value": "12 High St\nLeeds LS1 4AB"}],
            phones=[{"label": "", "value": "0113 496 0000"}, {"label": "", "value": "1"}],
        ),
    )
    initiative_stand_in.routes[("GET", "/contacts/4")] = lambda _r: httpx2.Response(
        200, json=_contact(4, "Mill & Co")
    )
    # Contact 8 is one the install may not read: Initiative answers 404.

    found = await recall.parties(initiative_stand_in.api(), community_ref, [9, 8, 4, 9])

    assert found == {9: CUSTOMER, 4: Party("Mill & Co")}


async def test_the_fda_spreadsheet_lists_shipments_in_the_templates_columns(
    db: WriteSession,
    main: Location,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    shipment = await ship(made.chai_item, "3", 5, lot=made.chai)
    shipment.reference = "BOL 42"
    gathered = await recall.gather(db, await trace.recall(db, lot_id=made.cinnamon.id))

    rows = list(csv.reader(io.StringIO(recall.fda_csv(gathered, CONTACTS))))

    assert rows[0] == list(FDA_COLUMNS)
    assert len(FDA_COLUMNS) == 36
    order_code = gathered.shipments[shipment.id][1].code
    assert rows[1:] == [
        [
            made.chai.number,
            "3",
            "each",
            "Chai",
            *[""] * 5,
            "Corner Café",
            "0113 496 0000",
            "12 High St, Leeds LS1 4AB",
            *[""] * 4,
            "Main location",
            *[""] * 6,
            "11/05/2026",
            "Main location",
            *[""] * 6,
            "",
            "Order",
            order_code,
            "Shipment reference",
            "BOL 42",
        ]
    ]


async def test_a_bought_lot_names_its_supplier_as_its_source(
    db: WriteSession,
    material,
    buy,
    main: Location,
):
    cinnamon = await material("Cinnamon")
    await buy((cinnamon, "5", "1"), supplier_contact_id=7)
    gathered = await recall.gather(db, await trace.recall(db, item_id=cinnamon.id))
    [lot_id] = gathered.lots
    names = recall._Names(gathered, CONTACTS)

    assert names.source(gathered.lots[lot_id]) == SUPPLIER
    assert recall._cell(SUPPLIER.name) == "'=Spice Co"
    assert gathered.contact_ids() == {7}


async def test_canadas_file_is_one_plain_text_file_of_lots_and_who_got_them(
    db: WriteSession,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    await ship(made.chai_item, "3", 5, lot=made.chai)
    gathered = await recall.gather(db, await trace.recall(db, lot_id=made.cinnamon.id))

    text = recall.canada_text(gathered, CONTACTS)

    lines = [line.split("\t") for line in text.removesuffix("\r\n").split("\r\n")]
    assert lines[0] == list(CANADA_COLUMNS)
    assert lines[1:] == [
        ["Cinnamon", made.cinnamon.number, "", "", "=Spice Co", "", "2026-11-01", "", "", "", ""],
        [
            "Chai spice",
            made.spice.number,
            "Main location",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "Cinnamon",
        ],
        ["Chai", made.chai.number, "Main location", "", "", "", "", "", "", "", "Chai spice"],
        [
            "Chai",
            made.chai.number,
            "Main location",
            "",
            "",
            "",
            "",
            "Corner Café",
            "12 High St, Leeds LS1 4AB",
            "2026-11-05",
            "",
        ],
    ]


async def test_the_pdf_writes_every_party_in_the_bundled_font(
    db: WriteSession,
    chai,  # noqa: F811
    ship,  # noqa: F811
):
    made = await chai()
    await ship(made.chai_item, "3", 5, lot=made.chai)
    await ship(made.chai_item, "1", 6)
    gathered = await recall.gather(db, await trace.recall(db, lot_id=made.cinnamon.id))

    written = recall.pdf(
        gathered, {9: Party("Café Ōsaka — Łódź", "1-2-3 Ginza", "")}, date(2026, 11, 12)
    )

    assert written.startswith(b"%PDF")
    assert len(written) > 1000
