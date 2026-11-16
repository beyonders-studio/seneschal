"""Hoard's migrated database keeps organizations apart, and its migrations
carry what is recorded forward."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError

from hoard.db import downgrade, upgrade
from seneschal_core import organizations
from seneschal_core.catalog import isolation_problems
from seneschal_core.db import Database, create_async_engine
from seneschal_core.testing import AppDatabase, migrated_database


async def test_every_table_keeps_the_organization_rules(database: AppDatabase):
    engine = create_async_engine(database.owner_url)
    try:
        async with engine.connect() as connection:
            problems = await isolation_problems(connection, serving_login=database.serving_login)
    finally:
        await engine.dispose()

    assert problems == []


async def _organization(served: Database, community_ref: str) -> int:
    async with served.session() as db:
        organization_id = await organizations.organization_for(
            db, community_ref=community_ref, plugin_install_id=1
        )
        await db.commit()
    return organization_id


async def _spend(db, organization_id: int, jti: str) -> None:
    await db.execute(
        text(
            "INSERT INTO handoff_redemptions (jti, organization_id, expires_at) "
            "VALUES (:jti, :org, :expires)"
        ),
        {"jti": jti, "org": organization_id, "expires": datetime.now(UTC) + timedelta(minutes=1)},
    )


async def _spent(db) -> list[str]:
    return list((await db.execute(text("SELECT jti FROM handoff_redemptions"))).scalars())


async def test_the_serving_login_reaches_one_organizations_rows_at_a_time(
    serving_database: Database, community_ref: str
):
    first = await _organization(serving_database, community_ref)
    second = await _organization(serving_database, f"{community_ref}-other")
    for organization_id in (first, second):
        async with serving_database.organization_session(organization_id) as db:
            await _spend(db, organization_id, f"{community_ref}-{organization_id}")
            await db.commit()

    async with serving_database.organization_session(first) as db:
        assert await _spent(db) == [f"{community_ref}-{first}"]
        with pytest.raises(DBAPIError, match="row-level security"):
            await _spend(db, second, f"{community_ref}-planted")

    async with serving_database.session() as db:
        assert await _spent(db) == []
        with pytest.raises(DBAPIError, match="row-level security"):
            await _spend(db, first, f"{community_ref}-stray")


def test_the_migrations_go_down_and_up_again():
    def round_trip(made: AppDatabase) -> None:
        upgrade(made.owner_url, made.serving_login)
        downgrade(made.owner_url, made.serving_login, "base")
        upgrade(made.owner_url, made.serving_login)

    with migrated_database("hoard_round_trip", round_trip):
        pass


def test_the_units_migration_matches_standard_units_and_merges_their_twins():
    def migrate(made: AppDatabase) -> None:
        upgrade(made.owner_url, made.serving_login, "0015")
        superuser = make_url(made.superuser_url).set(drivername="postgresql")
        with psycopg.connect(superuser.render_as_string(hide_password=False)) as db:
            [org] = (
                db.execute(
                    "INSERT INTO organizations (community_ref, plugin_install_id) "
                    "VALUES ('units', 1) RETURNING id"
                ).fetchone()
                or ()
            )

            def row(sql: str, *values: object) -> int:
                [found] = db.execute(sql, (org, *values)).fetchone() or ()
                return found

            unit = {
                name: row(
                    "INSERT INTO units (organization_id, name) VALUES (%s, %s) RETURNING id", name
                )
                for name in ("g", "Grams", "KG", "T", "Jar")
            }

            def material(name: str, tracking: str, bought: str, conversion: str) -> None:
                item = row(
                    "INSERT INTO items (organization_id, kind) VALUES (%s, 'material') RETURNING id"
                )
                db.execute(
                    "INSERT INTO materials (id, organization_id, name, tracking_unit_id, "
                    "purchase_unit_id, purchase_conversion) VALUES (%s, %s, %s, %s, %s, %s)",
                    (item, org, name, unit[tracking], unit[bought], conversion),
                )

            material("Catnip", "Grams", "KG", "999")
            material("Honey", "g", "Jar", "250")
            material("Salt", "g", "Grams", "3")

        upgrade(made.owner_url, made.serving_login)

    with migrated_database("hoard_units", migrate) as made:
        superuser = make_url(made.superuser_url).set(drivername="postgresql")
        with psycopg.connect(superuser.render_as_string(hide_password=False)) as db:
            units = db.execute("SELECT name, standard FROM units ORDER BY id").fetchall()
            materials = db.execute(
                "SELECT m.name, tracked.name, bought.name, m.purchase_conversion FROM materials m "
                "JOIN units tracked ON tracked.id = m.tracking_unit_id "
                "LEFT JOIN units bought ON bought.id = m.purchase_unit_id ORDER BY m.name"
            ).fetchall()

    assert units == [("g", "gram"), ("kg", "kilogram"), ("T", None), ("Jar", None)]
    assert materials == [
        ("Catnip", "g", "kg", Decimal("1000.000000")),
        ("Honey", "g", "Jar", Decimal("250.000000")),
        ("Salt", "g", None, Decimal("1.000000")),
    ]


def test_the_receipts_migration_dates_each_received_line_on_its_purchase():
    def migrate(made: AppDatabase) -> None:
        upgrade(made.owner_url, made.serving_login, "0016")
        superuser = make_url(made.superuser_url).set(drivername="postgresql")
        with psycopg.connect(superuser.render_as_string(hide_password=False)) as db:

            def row(sql: str, *values: object) -> int:
                [found] = db.execute(sql, values).fetchone() or ()
                return found

            org = row(
                "INSERT INTO organizations (community_ref, plugin_install_id) "
                "VALUES ('receipts', 1) RETURNING id"
            )
            unit = row(
                "INSERT INTO units (organization_id, name) VALUES (%s, 'g') RETURNING id", org
            )
            item = row(
                "INSERT INTO items (organization_id, kind) VALUES (%s, 'material') RETURNING id",
                org,
            )
            db.execute(
                "INSERT INTO materials (id, organization_id, name, tracking_unit_id) "
                "VALUES (%s, %s, 'Flour', %s)",
                (item, org, unit),
            )
            location = row(
                "INSERT INTO locations (organization_id, main) VALUES (%s, true) RETURNING id", org
            )
            purchase = row(
                "INSERT INTO purchases (organization_id, code, dated_on, location_id) "
                "VALUES (%s, 'P-1', '2026-11-08', %s) RETURNING id",
                org,
                location,
            )
            for received in (True, False):
                db.execute(
                    "INSERT INTO purchase_lines (organization_id, purchase_id, material_id, "
                    "purchase_quantity, tracking_quantity, received) VALUES (%s, %s, %s, 1, 1, %s)",
                    (org, purchase, item, received),
                )

        upgrade(made.owner_url, made.serving_login)

    with migrated_database("hoard_receipts", migrate) as made:
        superuser = make_url(made.superuser_url).set(drivername="postgresql")
        with psycopg.connect(superuser.render_as_string(hide_password=False)) as db:
            lines = db.execute("SELECT received_on FROM purchase_lines ORDER BY id").fetchall()

    assert lines == [(date(2026, 11, 8),), (None,)]
