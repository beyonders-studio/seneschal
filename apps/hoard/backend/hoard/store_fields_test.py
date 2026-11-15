"""A store's fields aligned with Hoard's: suggestions, and, against a store
kept in memory, a property filled at the sync and written through after it,
derived fields compared and sent only when asked or when a recipe changes
them, and a sync that writes nothing."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import col, select

from hoard import channels, recipes, store_fields, store_sync, sync
from hoard.app import JOB_HANDLERS
from hoard.channels import FieldValue, StoreField, StoreProduct, StoreVariant
from hoard.channels.fake import FakeConnector
from hoard.channels.hooks import webhook_address
from hoard.records import (
    Allergen,
    Channel,
    FieldOwner,
    FieldTarget,
    Location,
    Material,
    Product,
    PropertyDefinition,
    PropertyType,
    PropertyValue,
    Recipe,
    RecordKind,
    Store,
    StoreFieldValue,
    Variation,
)
from hoard.stock import add_product
from hoard.store_fields import FieldChoice
from seneschal_core import jobs
from seneschal_core.api import Plugin
from seneschal_core.db import WriteSession
from seneschal_core.jobs import Job, JobState
from seneschal_core.testing import InitiativeStandIn

ADDRESS = webhook_address(InitiativeStandIn.API_URL)
PRODUCT, VARIANT = FieldOwner.PRODUCT, FieldOwner.VARIANT


def _field(name: str, kind: str, owner: FieldOwner = PRODUCT, **choices: str) -> StoreField:
    key = name.lower().replace(" ", "_")
    return StoreField(owner, "custom", key, name, kind, choices=choices, count=3)


def _property(name: str, kind: PropertyType, number: int = 1) -> PropertyDefinition:
    return PropertyDefinition(id=number, kind=RecordKind.PRODUCT, name=name, type=kind)


def _suggested(store_field: StoreField, *properties: PropertyDefinition):
    found = store_fields.suggest(store_field, list(properties))
    return None if found is None else (found.target, found.property_id, dict(found.pairs))


# Suggestions.


def test_allergens_are_suggested_by_name_or_by_values():
    assert _suggested(_field("Allergens", "list.single_line_text_field")) == (
        FieldTarget.ALLERGENS,
        None,
        {},
    )
    entries = _field(
        "Contains",
        "list.metaobject_reference",
        **{"gid://m/1": "Milk", "gid://m/2": "Tree nuts", "gid://m/3": "Kiwi"},
    )
    assert _suggested(entries) == (
        FieldTarget.ALLERGENS,
        None,
        {"gid://m/1": "milk", "gid://m/2": "tree_nuts", "gid://m/3": None},
    )
    # A field whose entries are mostly not allergens is a property.
    colors = _field(
        "Color", "list.metaobject_reference", **{"gid://c/1": "Red", "gid://c/2": "Milk white"}
    )
    assert _suggested(colors) == (FieldTarget.PROPERTY, None, {})


def test_the_ingredient_list_is_suggested_by_name():
    assert _suggested(_field("Ingredients", "multi_line_text_field")) == (
        FieldTarget.INGREDIENTS,
        None,
        {},
    )


def test_an_existing_property_of_the_same_name_and_type_is_suggested():
    origin = _property("origin", PropertyType.TEXT, 7)
    assert _suggested(_field("Origin", "single_line_text_field"), origin) == (
        FieldTarget.PROPERTY,
        7,
        {},
    )
    # One of another type is not: a new one is.
    assert _suggested(_field("Origin", "number_integer"), origin) == (
        FieldTarget.PROPERTY,
        None,
        {},
    )


@pytest.mark.parametrize(
    ("kind", "choices", "expected"),
    [
        ("single_line_text_field", {}, PropertyType.TEXT),
        ("multi_line_text_field", {}, PropertyType.TEXT),
        ("number_integer", {}, PropertyType.NUMBER),
        ("number_decimal", {}, PropertyType.NUMBER),
        ("boolean", {}, PropertyType.CHECKBOX),
        ("date", {}, PropertyType.DATE),
        ("date_time", {}, PropertyType.DATETIME),
        ("url", {}, PropertyType.URL),
        ("single_line_text_field", {"Loose": "Loose", "Bagged": "Bagged"}, PropertyType.SELECT),
        ("list.single_line_text_field", {}, PropertyType.MULTI_SELECT),
        ("metaobject_reference", {"gid://g/1": "Loose"}, PropertyType.SELECT),
        ("list.metaobject_reference", {"gid://g/1": "Loose"}, PropertyType.MULTI_SELECT),
    ],
)
def test_a_new_property_takes_the_type_the_values_take(kind, choices, expected):
    store_field = _field("Leaf", kind, **choices)
    assert store_fields.property_type(store_field) is expected
    assert _suggested(store_field) == (FieldTarget.PROPERTY, None, {})


def test_a_field_nothing_holds_is_left_out():
    assert _suggested(_field("Weight", "weight")) is None
    assert _suggested(_field("Picture", "file_reference")) is None
    # A variant's field fills no product property.
    assert _suggested(_field("Origin", "single_line_text_field", VARIANT)) is None


# Against a store.


@pytest.fixture
def shop(
    monkeypatch: pytest.MonkeyPatch, initiative_stand_in: InitiativeStandIn, community_ref: str
) -> FakeConnector:
    """A store selling chai and a mug, with fields for each one's origin,
    allergens, ingredients and caffeine, and a weight nobody maps."""
    fake = FakeConnector()
    monkeypatch.setitem(channels.CONNECTORS, Channel.SHOPIFY, lambda values: fake)
    initiative_stand_in.connections[community_ref] = {"shopify": {"shop": fake.shop_domain}}
    fake.shop_products = {
        "p-chai": StoreProduct(
            id="p-chai",
            title="Chai",
            variants=[StoreVariant(id="v-chai", title="Chai", sku="CHAI", inventory_item_id="i-1")],
        ),
        "p-mug": StoreProduct(
            id="p-mug",
            title="Mug",
            variants=[StoreVariant(id="v-mug", title="Mug", sku="MUG", inventory_item_id="i-2")],
        ),
    }
    fake.shop_fields = [
        _field("Origin", "single_line_text_field"),
        _field("Allergens", "list.single_line_text_field"),
        _field("Ingredients", "multi_line_text_field"),
        _field("Caffeine", "boolean"),
        _field("Weight", "weight"),
    ]
    fake.field_store = {
        "p-chai": {
            "custom.origin": "Assam",
            "custom.allergens": '["Milk"]',
            "custom.caffeine": "true",
            "custom.weight": '{"value": 1, "unit": "KILOGRAMS"}',
        },
        "p-mug": {"custom.origin": "Stoke"},
    }
    return fake


@pytest.fixture
async def store(db: WriteSession) -> Store:
    made = Store(
        organization_id=db.organization_id,
        channel=Channel.SHOPIFY,
        shop_domain="tea.myshopify.com",
        connected=True,
    )
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
async def caffeine(db: WriteSession) -> PropertyDefinition:
    made = PropertyDefinition(
        organization_id=db.organization_id,
        kind=RecordKind.PRODUCT,
        name="Caffeine",
        type=PropertyType.CHECKBOX,
    )
    db.add(made)
    await db.flush()
    return made


@pytest.fixture
async def chai(db: WriteSession, material) -> Variation:
    """Hoard's chai, made with almond milk, before the store is synced."""
    _, variation = await add_product(db, Product(name="Chai"))
    variation.sku = "CHAI"
    db.add(variation)
    almond: Material = await material("Almond milk", allergens=[Allergen.TREE_NUTS])
    await recipes.save(db, Recipe(variation_id=variation.id), [recipes.Line(almond.id, Decimal(1))])
    return variation


@pytest.fixture
def plugin(app) -> Plugin:
    return app.state.plugin


async def _work(db: WriteSession, plugin: Plugin) -> list[Job]:
    """Work every job waiting, and answer them all."""
    await db.commit()
    await jobs.work(plugin, db.organization_id, JOB_HANDLERS)
    return list(
        await db.exec(select(Job).order_by(col(Job.id)).execution_options(populate_existing=True))
    )


@pytest.fixture
async def synced(
    db: WriteSession,
    store: Store,
    main: Location,
    chai: Variation,
    caffeine: PropertyDefinition,
    shop: FakeConnector,
    plugin: Plugin,
) -> Store:
    """The store synced, its origin into a new property, its caffeine into
    Hoard's, and its allergens and ingredients compared with Hoard's."""
    choices = [
        FieldChoice(PRODUCT, "custom.origin", FieldTarget.PROPERTY),
        FieldChoice(PRODUCT, "custom.caffeine", FieldTarget.PROPERTY, caffeine.id),
        FieldChoice(PRODUCT, "custom.allergens", FieldTarget.ALLERGENS),
        FieldChoice(PRODUCT, "custom.ingredients", FieldTarget.INGREDIENTS),
    ]
    await jobs.enqueue(
        db,
        store_sync.STORE_SYNC_JOB,
        store_sync.job_args(store, ADDRESS, [store_sync.LocationChoice("loc-1", main.id)], choices),
        started_by="member-ref",
        unique_key=str(store.id),
    )
    [job] = await _work(db, plugin)
    assert (job.state, job.error) == (JobState.DONE, None)
    return await db.get_one(Store, store.id, populate_existing=True)


async def _properties(db: WriteSession) -> dict[tuple[str, str], object]:
    rows = await db.exec(
        select(Product.name, PropertyDefinition.name, PropertyValue.value)
        .join(PropertyValue, col(PropertyValue.product_id) == Product.id)
        .join(PropertyDefinition, col(PropertyDefinition.id) == PropertyValue.definition_id)
        .execution_options(populate_existing=True)
    )
    return {(product, name): value for product, name, value in rows.all()}


async def _differing(db: WriteSession, store: Store) -> list[tuple[str | None, str, list, list]]:
    return [
        (
            None if found.product is None else found.product.name,
            found.mapping.name,
            found.hoard,
            found.store,
        )
        for found in await store_fields.differing(db, store)
    ]


async def test_the_sync_fills_mapped_properties_and_writes_nothing(
    db: WriteSession, synced: Store, shop: FakeConnector
):
    origin = (
        await db.exec(select(PropertyDefinition).where(PropertyDefinition.name == "Origin"))
    ).one()
    assert (origin.kind, origin.type) == (RecordKind.PRODUCT, PropertyType.TEXT)
    assert await _properties(db) == {
        ("Chai", "Origin"): "Assam",
        ("Chai", "Caffeine"): True,
        ("Mug", "Origin"): "Stoke",
    }
    recorded = await db.exec(
        select(
            StoreFieldValue.owner_id, StoreFieldValue.store_value, StoreFieldValue.hoard_value
        ).order_by(col(StoreFieldValue.id))
    )
    assert sorted(recorded.all(), key=str) == sorted(
        [
            ("p-chai", '["Milk"]', '["Tree nuts"]'),
            ("p-chai", None, "Almond milk"),
            ("p-mug", None, None),
            ("p-mug", None, None),
        ],
        key=str,
    )
    assert (shop.field_writes, shop.pushes) == ([], [])


async def test_differing_values_are_listed_and_sent_only_when_asked(
    db: WriteSession, synced: Store, shop: FakeConnector, plugin: Plugin, client, sign_in
):
    differing = [
        ("Chai", "Allergens", ["Tree nuts"], ["Milk"]),
        ("Chai", "Ingredients", ["Almond milk"], []),
    ]
    assert await _differing(db, synced) == differing
    # Hoard's own values have not changed since the sync: nothing is sent.
    await store_fields.queue_changed(db)
    await _work(db, plugin)
    assert shop.field_writes == []

    await sign_in(client, admin=True)
    listed = (await client.get(f"/api/v1/stores/{synced.id}/fields/differing")).json()
    assert [(row["product_name"], row["hoard"], row["store"]) for row in listed] == [
        ("Chai", ["Tree nuts"], ["Milk"]),
        ("Chai", ["Almond milk"], []),
    ]
    answer = await client.post(f"/api/v1/stores/{synced.id}/fields/send")
    assert answer.status_code == 202, answer.text
    await _work(db, plugin)

    assert sorted(shop.field_writes, key=lambda value: value.handle) == [
        FieldValue("p-chai", "custom.allergens", "list.single_line_text_field", '["Tree nuts"]'),
        FieldValue("p-chai", "custom.ingredients", "multi_line_text_field", "Almond milk"),
    ]
    assert await _differing(db, synced) == []


async def test_a_recipe_change_sends_what_it_changes(
    db: WriteSession, synced: Store, shop: FakeConnector, plugin: Plugin, material
):
    mug = (await db.exec(select(Variation).where(Variation.sku == "MUG"))).one()
    clay = await material("Clay", allergens=[])
    await recipes.save(db, Recipe(variation_id=mug.id), [recipes.Line(clay.id, Decimal(1))])
    await _work(db, plugin)

    # The mug's ingredients changed and are sent; its allergens, none
    # before and none now, are not; the chai's, which differ but did not
    # change, wait to be sent.
    assert shop.field_writes == [
        FieldValue("p-mug", "custom.ingredients", "multi_line_text_field", "Clay")
    ]
    assert [row[:2] for row in await _differing(db, synced)] == [
        ("Chai", "Allergens"),
        ("Chai", "Ingredients"),
    ]


async def test_a_mapped_property_is_written_through_and_read_back(
    db: WriteSession, synced: Store, shop: FakeConnector, plugin: Plugin, client, sign_in
):
    chai = (await db.exec(select(Product).where(Product.name == "Chai"))).one()
    origin = (
        await db.exec(select(PropertyDefinition).where(PropertyDefinition.name == "Origin"))
    ).one()
    await sign_in(client)

    answer = await client.put(
        f"/api/v1/properties/values/product/{chai.id}",
        json={"values": [{"property_id": origin.id, "value": "Darjeeling"}]},
    )

    assert answer.status_code == 200, answer.text
    assert shop.field_writes == [
        FieldValue("p-chai", "custom.origin", "single_line_text_field", "Darjeeling")
    ]
    assert (await _properties(db))[("Chai", "Origin")] == "Darjeeling"

    # A change made in the store comes back through the reconcile.
    shop.field_store["p-chai"]["custom.origin"] = "Nilgiri"
    del shop.field_store["p-chai"]["custom.caffeine"]
    await sync.reconcile(plugin, db.organization_id, synced, shop, None)

    held = await _properties(db)
    assert held[("Chai", "Origin")] == "Nilgiri"
    assert ("Chai", "Caffeine") not in held
    assert len(shop.field_writes) == 1


async def test_a_value_the_store_refuses_is_not_kept(
    db: WriteSession, synced: Store, shop: FakeConnector, client, sign_in
):
    chai = (await db.exec(select(Product).where(Product.name == "Chai"))).one()
    origin = (
        await db.exec(select(PropertyDefinition).where(PropertyDefinition.name == "Origin"))
    ).one()
    shop.refuse = channels.ConnectError.STORE_UNREACHABLE
    await sign_in(client)

    answer = await client.put(
        f"/api/v1/properties/values/product/{chai.id}",
        json={"values": [{"property_id": origin.id, "value": "Darjeeling"}]},
    )

    assert (answer.status_code, answer.json()["detail"]) == (502, "STORE_UNREACHABLE")
    assert (await _properties(db))[("Chai", "Origin")] == "Assam"


async def test_a_field_mapped_later_is_read_in_as_a_job(
    db: WriteSession, synced: Store, shop: FakeConnector, plugin: Plugin, client, sign_in
):
    shop.shop_fields.append(_field("Grade", "single_line_text_field"))
    shop.field_store["p-mug"]["custom.grade"] = "First"
    await sign_in(client, admin=True)

    listed = (await client.get(f"/api/v1/stores/{synced.id}/fields")).json()
    grade = next(row for row in listed if row["handle"] == "custom.grade")
    assert (grade["suggested"]["target"], grade["mapped"]) == ("property", None)
    answer = await client.put(
        f"/api/v1/stores/{synced.id}/fields",
        json={"owner": "product", "handle": "custom.grade", "target": "property"},
    )
    assert answer.status_code == 200, answer.text
    await _work(db, plugin)

    assert (await _properties(db))[("Mug", "Grade")] == "First"
    assert shop.field_writes == []
