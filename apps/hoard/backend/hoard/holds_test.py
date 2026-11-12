"""What a held lot keeps from use: its id among the held lots, and its balance
from what is available."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard import holds
from hoard.records import HoldEnd, Location, Product
from hoard.stock import add_product
from seneschal_core.db import WriteSession


async def test_only_open_holds_keep_a_lot_and_its_balance(
    db: WriteSession, main: Location, material, lot, hold
):
    flour = await material("Flour")
    shed = Location(organization_id=db.organization_id, name="Shed")
    db.add(shed)
    await db.flush()
    held = await lot(flour.id, "F-1", "4")
    away = await lot(flour.id, "F-2", "3", location=shed)
    released = await lot(flour.id, "F-3", "2")
    await lot(flour.id, "F-4", "5")
    await hold(held)
    await hold(away)
    await hold(released, ended=HoldEnd.RELEASED, ended_on=date(2026, 11, 3))

    assert await holds.held_lot_ids(db) == {held.id, away.id}
    assert await holds.held_lot_ids(db, flour.id, main.id) == {held.id}
    assert await holds.on_hold(db, flour.id) == 7
    assert await holds.on_hold(db, flour.id, shed.id) == 3


async def test_starting_or_ending_a_hold_notes_its_item_for_its_listings(db: WriteSession, lot):
    _, chai = await add_product(db, Product(name="Chai"))
    made = await lot(chai.id, "C-1", "6")

    await holds.note(db, made.id)

    assert db.info["hoard.noted_items"] == {db.organization_id: {chai.id}}
    assert await holds.on_hold(db, chai.id) == Decimal(0)
