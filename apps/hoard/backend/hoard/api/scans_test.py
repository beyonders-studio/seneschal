"""What a scan names in Hoard: the item and the lot a label is for."""

from __future__ import annotations

from datetime import date

from hoard.records import Location
from seneschal_core.db import WriteSession

SCANS = "/api/v1/scans"
GTIN = "09506000134352"
GS = "\x1d"


async def scan(client, text: str, **ask: object) -> dict:
    answered = await client.post(SCANS, json={"text": text, **ask})
    assert answered.status_code == 200, answered.text
    return answered.json()


async def test_a_gs1_label_names_its_item_and_lot(client, sign_in, db: WriteSession, material, lot):
    sencha = await material("Sencha", gtin=GTIN)
    held = await lot(sencha.id, "L24-118", "5", expires_on=date(2027, 6, 30))
    await lot(sencha.id, "L24-119", "5")
    await db.commit()
    await sign_in(client)

    read = await scan(client, f"]d201{GTIN}17270630{GS}10L24-118")

    assert (read["kind"], read["gtin"], read["lot"], read["expires_on"]) == (
        "gs1",
        GTIN,
        "L24-118",
        "2027-06-30",
    )
    assert [(item["id"], item["name"], item["path"]) for item in read["items"]] == [
        (sencha.id, "Sencha", f"/stock/materials/{sencha.id}")
    ]
    assert [(each["id"], each["on_hold"], each["loose"]) for each in read["lots"]] == [
        (held.id, False, False)
    ]
    assert read["problems"] == []


async def test_a_plain_code_names_items_by_sku_and_lots_by_number(
    client, sign_in, db: WriteSession, material, product, lot
):
    sencha = await material("Sencha", sku="TEA-1")
    tin = await product("Tea tin")
    made = await lot(tin.id, "R-0012", "3")
    await db.commit()
    await sign_in(client)

    by_sku = await scan(client, " tea-1 ")
    by_lot = await scan(client, "R-0012")

    assert (by_sku["kind"], [item["id"] for item in by_sku["items"]]) == ("plain", [sencha.id])
    assert by_sku["lots"] == []
    assert [each["id"] for each in by_lot["lots"]] == [made.id]
    assert by_lot["lots"][0]["path"] == f"/stock/lots/{made.id}"


async def test_an_own_numeric_sku_that_reads_as_a_gtin_is_still_found(
    client, sign_in, db: WriteSession, material
):
    # 96385074 has a good EAN-8 check digit, but here it is a SKU.
    sencha = await material("Sencha", sku="96385074")
    await db.commit()
    await sign_in(client)

    read = await scan(client, "96385074")

    assert [item["id"] for item in read["items"]] == [sencha.id]
    assert "unknown_gtin" not in read["problems"]


async def test_a_label_says_what_hoard_doesnt_hold_or_wont_ship(
    client, sign_in, db: WriteSession, material, lot, hold
):
    sencha = await material("Sencha", gtin=GTIN)
    other = await material("Matcha")
    held = await lot(sencha.id, "L1", "5", expires_on=date(2027, 1, 31))
    await hold(held)
    await db.commit()
    await sign_in(client)

    unknown = await scan(client, "(01)00012345678905(10)L1")
    missing = await scan(client, f"(01){GTIN}(10)NOPE")
    on_hold = await scan(client, f"(01){GTIN}(10)L1(17)270630", item_id=other.id)

    assert unknown["problems"] == ["unknown_gtin"]
    assert missing["problems"] == ["lot_not_found"]
    assert set(on_hold["problems"]) == {"item_mismatch", "lot_on_hold", "expiry_differs"}
    assert on_hold["lots"][0]["on_hold"] is True


async def test_lots_are_kept_to_the_pages_location_and_match_loosely_last(
    client, sign_in, db: WriteSession, material, lot, main: Location
):
    sencha = await material("Sencha", gtin=GTIN)
    back = Location(organization_id=db.organization_id, name="Back room")
    db.add(back)
    await db.flush()
    here = await lot(sencha.id, "ab-7", "1")
    await lot(sencha.id, "ab-7", "1", location=back)
    await db.commit()
    await sign_in(client)

    read = await scan(client, f"(01){GTIN}(10)AB-7", location_id=main.id)

    assert [(each["id"], each["loose"]) for each in read["lots"]] == [(here.id, True)]


async def test_a_dropped_separator_is_said_to_be_ambiguous(
    client, sign_in, db: WriteSession, material
):
    await material("Sencha", gtin=GTIN)
    await db.commit()
    await sign_in(client)

    read = await scan(client, f"01{GTIN}10L24-11817270630")

    assert "ambiguous" in read["problems"]
