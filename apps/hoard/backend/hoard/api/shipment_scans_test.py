"""Scanning lots onto a shipment, and the categories that require it."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard.records import Category, CategoryKind, LotScan, Product, Variation
from hoard.stock import add_product
from seneschal_core.db import WriteSession

GTIN = "09506000134352"
SHIPPED_ON = {"shipped_on": "2026-11-18"}


async def chai(db: WriteSession, **category: object) -> Variation:
    """A product with the GTIN on its label, in a category of its own."""
    teas = Category(
        organization_id=db.organization_id, kind=CategoryKind.PRODUCT, name="Teas", **category
    )
    db.add(teas)
    await db.flush()
    _, variation = await add_product(
        db, Product(name="Chai", default_price=Decimal(4), category_id=teas.id)
    )
    variation.gtin = GTIN
    await db.flush()
    return variation


async def ship_planned(client, item_id: int, quantity: str) -> tuple[dict, int]:
    """An order of ``quantity``, and its shipment planned: the shipment's id."""
    order = (
        await client.post(
            "/api/v1/orders",
            json={"customer_contact_id": 7, "lines": [{"item_id": item_id, "quantity": quantity}]},
        )
    ).json()
    planned = (await client.post("/api/v1/shipments", json={"order_id": order["id"]})).json()
    return planned, planned["shipments"][0]["id"]


def label(lot: str) -> str:
    return f"(01){GTIN}(10){lot}"


async def test_a_scanned_label_places_its_lot_on_the_items_line(
    client, sign_in, db: WriteSession, lot
):
    tea = await chai(db)
    await lot(tea.id, "C-1", "10", date(2026, 10, 1))
    later = await lot(tea.id, "C-2", "10", date(2026, 11, 1))
    await db.commit()
    await sign_in(client)
    _, shipment = await ship_planned(client, tea.id, "4")

    scanned = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-2")})

    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert (body["lot_id"], body["split"], body["not_first"]) == (later.id, False, True)
    [line] = body["order"]["shipments"][0]["lines"]
    assert (line["lot"]["number"], line["lot_source"], line["assumed"]) == ("C-2", "scanned", False)
    # Scanning the same lot again changes nothing.
    again = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-2")})
    assert again.json()["line_id"] == body["line_id"]
    shipped = await client.post(f"/api/v1/shipments/{shipment}/ship", json=SHIPPED_ON)
    assert shipped.status_code == 200, shipped.text


async def test_a_lot_holding_less_than_the_line_takes_what_it_holds_and_leaves_the_rest(
    client, sign_in, db: WriteSession, lot
):
    tea = await chai(db)
    await lot(tea.id, "C-1", "3")
    await lot(tea.id, "C-2", "10")
    await lot(tea.id, "C-3", "10")
    await db.commit()
    await sign_in(client)
    _, shipment = await ship_planned(client, tea.id, "5")

    first = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-1")})
    second = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-2")})

    assert (first.json()["split"], second.json()["split"]) == (True, False)
    lines = second.json()["order"]["shipments"][0]["lines"]
    assert [(line["lot"]["number"], line["quantity"], line["lot_source"]) for line in lines] == [
        ("C-1", "3.000000", "scanned"),
        ("C-2", "2.000000", "scanned"),
    ]
    # The same case scanned twice changes nothing; another lot finds nothing left.
    again = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-1")})
    assert (again.status_code, again.json()["split"]) == (200, False)
    done = await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-3")})
    assert (done.status_code, done.json()) == (409, {"detail": "SCAN_ITEM_DONE"})


async def test_a_scan_says_why_it_placed_no_lot(
    client, sign_in, db: WriteSession, lot, hold, material
):
    tea = await chai(db)
    held = await lot(tea.id, "C-1", "10")
    await hold(held)
    other = await material("Sencha", gtin="00012345678905")
    await lot(other.id, "S-1", "5")
    await db.commit()
    await sign_in(client)
    _, shipment = await ship_planned(client, tea.id, "1")
    path = f"/api/v1/shipments/{shipment}/scans"

    on_hold = await client.post(path, json={"text": label("C-1")})
    not_here = await client.post(path, json={"text": "(01)00012345678905(10)S-1"})
    no_lot = await client.post(path, json={"text": GTIN})
    no_such = await client.post(path, json={"text": label("NOPE")})
    ambiguous = await client.post(path, json={"text": f"01{GTIN}10C-117270630"})

    assert (on_hold.status_code, on_hold.json()) == (409, {"detail": "LOT_ON_HOLD"})
    assert (not_here.status_code, not_here.json()) == (422, {"detail": "SCAN_NOT_ON_SHIPMENT"})
    assert no_lot.json() == {"detail": "SCAN_NAMES_NO_LOT"}
    assert no_such.json() == {"detail": "SCAN_LOT_NOT_FOUND"}
    assert ambiguous.json() == {"detail": "SCAN_AMBIGUOUS"}


async def test_where_a_category_requires_a_scan_only_a_scan_or_an_admin_chooses_the_lot(
    client, sign_in, db: WriteSession, lot
):
    tea = await chai(db, lot_scan=LotScan.REQUIRED)
    one = await lot(tea.id, "C-1", "10")
    await db.commit()
    await sign_in(client)
    order, shipment = await ship_planned(client, tea.id, "2")
    line_id = order["shipments"][0]["lines"][0]["id"]
    assert order["shipments"][0]["lines"][0]["lot_scan"] == "required"

    assumed = await client.post(f"/api/v1/shipments/{shipment}/ship", json=SHIPPED_ON)
    await client.patch(f"/api/v1/shipments/{shipment}/lines/{line_id}", json={"lot_id": one.id})
    chosen = await client.post(f"/api/v1/shipments/{shipment}/ship", json=SHIPPED_ON)

    assert (assumed.status_code, assumed.json()) == (409, {"detail": "LOT_SCAN_REQUIRED"})
    assert chosen.json() == {"detail": "LOT_SCAN_REQUIRED"}

    await sign_in(client, admin=True)
    by_admin = await client.patch(
        f"/api/v1/shipments/{shipment}/lines/{line_id}", json={"lot_id": one.id}
    )
    assert by_admin.json()["shipments"][0]["lines"][0]["lot_source"] == "admin"
    shipped = await client.post(f"/api/v1/shipments/{shipment}/ship", json=SHIPPED_ON)
    assert shipped.status_code == 200, shipped.text


async def test_a_scan_satisfies_a_category_that_requires_one(
    client, sign_in, db: WriteSession, lot
):
    tea = await chai(db, lot_scan=LotScan.REQUIRED)
    await lot(tea.id, "C-1", "10")
    await db.commit()
    await sign_in(client)
    _, shipment = await ship_planned(client, tea.id, "2")

    await client.post(f"/api/v1/shipments/{shipment}/scans", json={"text": label("C-1")})
    shipped = await client.post(f"/api/v1/shipments/{shipment}/ship", json=SHIPPED_ON)

    assert shipped.status_code == 200, shipped.text


async def test_an_admin_sets_a_categorys_scanning(client, sign_in, db: WriteSession):
    teas = Category(organization_id=db.organization_id, kind=CategoryKind.PRODUCT, name="Teas")
    db.add(teas)
    await db.commit()
    await sign_in(client, admin=True)

    changed = await client.patch(
        f"/api/v1/settings/categories/{teas.id}", json={"lot_scan": "warn"}
    )

    assert changed.json()["lot_scan"] == "warn"
