"""The community's connected stores: each one's state, its sync, its
locations mapped to Hoard's, its push log, and its products.

A store is pending, syncing or synced (``hoard.store_sync``). Syncing it, and
removing its sync, is an administrator's, as are reading the store again and
mapping its locations.

A store's fields are mapped to what they fill in Hoard (``hoard.store_fields``):
in the sync's request, and, once it is synced, one at a time, each new one
read in as a job. The products whose allergens or ingredient list differ
from the store's are listed, and Hoard's values sent only when asked.

A store's products are shown from Hoard's copy of them (``hoard.listings``),
which the store's webhooks and the reconcile keep up to date, and written
back to the store as they are edited; the store's answer is kept as the copy.
An edit names the version of the product it started from, the store's
``updated_at`` as the copy held it, and is sent only while the store holds
that version (:func:`started_from`).
Prices are sent and taken only for a member who sees costs. A Hoard product
can be made in a store, its listings linked to its variations at once.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import channels, listings, push, store_fields, store_sync
from hoard.api import items
from hoard.api.deps import AdminDep, HoardError, SeesCostsDep
from hoard.api.listings import ListingRead, reads
from hoard.api.settings import found
from hoard.api.shapes import Page, PageDep
from hoard.channels import (
    Connector,
    ConnectRefused,
    StoreField,
    StoreImage,
    StoreProduct,
    StoreVariant,
)
from hoard.records import (
    Allergen,
    Channel,
    FieldOwner,
    FieldTarget,
    Listing,
    Location,
    MatchedBy,
    Product,
    PropertyDefinition,
    PropertyType,
    PushStatus,
    RecordKind,
    StockPush,
    Store,
    StoreFieldMapping,
    StoreLocation,
    StoreProductCopy,
    Variation,
)
from hoard.store_fields import FieldChoice, StoreFieldRefused
from seneschal_core import jobs
from seneschal_core.api import (
    OrganizationSessionDep,
    Plugin,
    PluginDep,
    SessionDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.db import WriteSession
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable
from seneschal_core.jobs import JobRead

router = APIRouter(prefix="/stores", tags=["stores"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_LIVE: dict[int | str, dict[str, Any]] = {
    **_READ,
    409: {"description": "STORE_NOT_CONNECTED"},
    502: {"description": "STORE_UNREACHABLE and the store's other refusals"},
    503: {"description": "INITIATIVE_UNAVAILABLE"},
}
_CHANGE_LIVE: dict[int | str, dict[str, Any]] = {
    **_LIVE,
    403: {"description": "READ_ONLY"},
    409: {"description": "STORE_NOT_CONNECTED, STORE_NOT_SYNCED, STORE_PRODUCT_CHANGED"},
}
_ADMIN: dict[int | str, dict[str, Any]] = {
    **_CHANGE_LIVE,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
}


class StoreError(StrEnum):
    """Why a store's request was refused, beside the store's own refusals
    (:class:`hoard.channels.ConnectError`)."""

    #: The store's connection is not saved now.
    STORE_NOT_CONNECTED = "STORE_NOT_CONNECTED"
    #: The location named is not one of the organization's.
    LOCATION_UNKNOWN = "LOCATION_UNKNOWN"
    #: The store is synced already: its sync is removed first.
    STORE_SYNCED = "STORE_SYNCED"
    #: The store is not synced yet.
    STORE_NOT_SYNCED = "STORE_NOT_SYNCED"
    #: The store's sync is running.
    STORE_SYNCING = "STORE_SYNCING"
    #: The store's product changed since the edit started: nothing was sent,
    #: and Hoard's copy now holds the store's version.
    STORE_PRODUCT_CHANGED = "STORE_PRODUCT_CHANGED"


class StoreState(StrEnum):
    """Where a store's sync stands."""

    #: Connected, and not synced: Hoard reads and writes nothing of it.
    PENDING = "pending"
    #: Its sync is waiting or running.
    SYNCING = "syncing"
    #: Synced: it and Hoard keep each other up to date.
    SYNCED = "synced"


def _webhook_address(request: Request) -> str:
    return request.app.state.webhook_address


#: Initiative's address for Hoard's webhooks.
WebhookAddressDep = Annotated[str, Depends(_webhook_address)]


class StoreLocationRead(BaseModel):
    id: int
    #: The store's id and name for it.
    external_id: str
    name: str | None
    #: The Hoard location whose stock it sells; none until it is mapped.
    location_id: int | None


class StoreLocationWrite(BaseModel):
    """The Hoard location whose stock a store location sells, or null for none."""

    location_id: int | None


class StoreRead(BaseModel):
    id: int
    channel: Channel
    shop_domain: str | None
    account_label: str | None
    connected: bool
    #: Why saving the connection was last refused: a code pages translate.
    last_check_error: str | None
    state: StoreState
    #: Since when it is synced; null while it is not.
    imported_at: datetime | None
    #: Its sync running or waiting, else the last one it had.
    sync_job_id: int | None
    locations: list[StoreLocationRead]
    #: Its listings, and how many of them sell an item.
    listings: int
    linked: int
    #: Its pushes waiting to be sent, and those given up on.
    pushes_queued: int
    pushes_failed: int


class StorePushRead(BaseModel):
    """One push of the push log."""

    id: int
    listing_id: int
    #: The listing's title and SKU.
    title: str | None
    sku: str | None
    #: The store location's name.
    location: str | None
    quantity: int
    compare_quantity: int | None
    status: PushStatus
    attempts: int
    #: Why it was not taken: a code pages translate.
    error: str | None
    created_at: datetime
    pushed_at: datetime | None


class StoreLocationFound(BaseModel):
    """One of the store's locations, as the store has it now."""

    external_id: str
    name: str


class StoreLocationChoice(BaseModel):
    """Where a store location's stock goes once the store is synced: a Hoard
    location, a new one named as the store names it, or, with neither,
    nowhere: its stock is not read and nothing is pushed there."""

    external_id: str = Field(min_length=1, max_length=200)
    location_id: int | None = None
    new: bool = False


class StoreFieldChoiceWrite(BaseModel):
    """What one of the store's fields fills: Hoard's allergens or ingredient
    list, or a product property, by its id, or, with none, a new one named
    as the store names the field. ``pairs`` pairs an allergens field's values
    with Hoard's allergens by hand, null for none."""

    owner: FieldOwner
    handle: str = Field(min_length=3, max_length=300)
    target: FieldTarget
    property_id: int | None = None
    pairs: dict[str, Allergen | None] = {}

    def choice(self) -> FieldChoice:
        return FieldChoice(
            self.owner,
            self.handle,
            self.target,
            self.property_id,
            {
                value: None if allergen is None else allergen.value
                for value, allergen in self.pairs.items()
            },
        )


class StoreSyncWrite(BaseModel):
    """Where each of the store's locations takes its stock from, one not
    named taking none; and what each of its fields fills, one not named
    being left out."""

    locations: list[StoreLocationChoice] = []
    fields: list[StoreFieldChoiceWrite] = []


class StoreFieldEntry(BaseModel):
    """An entry a field picks from: the value the store keeps, and its name."""

    value: str
    name: str


class StoreFieldTargetRead(BaseModel):
    """What a field fills, or Hoard suggests it fill."""

    target: FieldTarget
    #: The property, for a property; null for a new one.
    property_id: int | None
    #: Each of an allergens field's values, paired with an allergen or not yet.
    pairs: dict[str, Allergen | None]


class StoreFieldRead(BaseModel):
    """One of the fields the store defines, as it has them now."""

    owner: FieldOwner
    handle: str
    name: str
    #: The store's name for its type.
    type: str
    #: How many products or variants hold a value of it.
    count: int
    #: What it picks from, where it picks from a fixed list.
    entries: list[StoreFieldEntry]
    #: The type of a property holding its values; null where none can.
    property_type: PropertyType | None
    #: Whether it can hold Hoard's allergens, and its ingredient list.
    allergens: bool
    ingredients: bool
    #: What Hoard suggests it fill; null to leave it out.
    suggested: StoreFieldTargetRead | None
    #: What it fills now, once mapped.
    mapping_id: int | None
    mapped: StoreFieldTargetRead | None


class StoreFieldDifferenceRead(BaseModel):
    """A product whose value of a field Hoard works out differs from the store's."""

    mapping_id: int
    #: The store's name for the field.
    field: str
    target: FieldTarget
    #: The store's id for the product or variant.
    owner_id: str
    #: The Hoard product, where it is linked.
    product_id: int | None
    product_name: str | None
    #: Each value, as a list of its entries.
    hoard: list[str]
    store: list[str]


class StoreImageRead(BaseModel):
    id: str | None
    url: str
    alt: str | None


class StoreVariantRead(BaseModel):
    id: str | None
    title: str
    sku: str | None
    #: Null without ``costs``.
    price: Decimal | None
    options: dict[str, str]


class StoreProductRead(BaseModel):
    """A store's product, as Hoard's copy of it holds it."""

    store_id: int
    id: str
    title: str
    #: As the store keeps it: HTML.
    description: str | None
    #: The store's word for whether it sells it, such as ``active`` or ``draft``.
    status: str | None
    options: list[str]
    variants: list[StoreVariantRead]
    images: list[StoreImageRead]
    #: The store's sales channels showing it, by the store's ids.
    published_on: list[str]
    product_type: str | None
    vendor: str | None
    tags: list[str]
    #: When the store last changed it.
    updated_at: datetime | None
    #: Its listings, each with the item it sells.
    listings: list[ListingRead]


class StoreImageWrite(BaseModel):
    """An image: one the store holds by its id, or a new one by its address."""

    id: str | None = None
    url: str = Field(min_length=1, max_length=2000)
    alt: str | None = Field(default=None, max_length=500)


class StoreVariantWrite(BaseModel):
    """A variant's edits, by the store's id for it. A price is taken only from
    a member who sees costs."""

    id: str = Field(min_length=1, max_length=200)
    sku: items.OptionalText = None
    price: items.Amount | None = None


class StoreProductWrite(BaseModel):
    """A store product as an edit leaves it. Variants it does not name stay
    as they are; its images are the ones it lists, in order."""

    #: When the store last changed it, as read when the edit started.
    updated_at: datetime | None
    title: items.Text
    description: str | None = Field(default=None, max_length=100_000)
    status: str | None = Field(default=None, max_length=50)
    variants: list[StoreVariantWrite] = []
    images: list[StoreImageWrite] = []
    published_on: list[str] = []


class StoreProductCreate(BaseModel):
    """The Hoard product to make in the store, with a variant per variation."""

    product_id: int


def _field_refused(refused: StoreFieldRefused) -> HTTPException:
    return refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code)


def store_refused(refused: ConnectRefused) -> HTTPException:
    """The answer to the store refusing what Hoard asked."""
    return refuse(status.HTTP_502_BAD_GATEWAY, refused.code)


def _require_synced(store: Store) -> None:
    """Refuse a write to a store that is not synced: Hoard writes nothing to it."""
    if store.imported_at is None:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_NOT_SYNCED)


async def store_connector(plugin: Plugin, community_ref: str, store: Store) -> Connector:
    """The store's connector, from the values its connection holds now."""
    if not store.connected:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_NOT_CONNECTED)
    try:
        connector = await channels.connect(plugin, community_ref, store.channel)
    except (InitiativeUnavailable, InitiativeRefused):
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE
        ) from None
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    if connector is None:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_NOT_CONNECTED)
    return connector


async def _store_read(db: AsyncSession, store: Store) -> StoreRead:
    located = await db.exec(
        select(StoreLocation)
        .where(StoreLocation.store_id == store.id)
        .order_by(col(StoreLocation.name), col(StoreLocation.id))
    )
    total, linked = (
        await db.exec(
            select(func.count(), func.count(col(Listing.item_id))).where(
                Listing.store_id == store.id
            )
        )
    ).one()
    queued, failed = (
        await db.exec(
            select(
                func.count().filter(col(StockPush.status) == PushStatus.QUEUED),
                func.count().filter(col(StockPush.status) == PushStatus.FAILED),
            )
            .join(Listing, col(Listing.id) == StockPush.listing_id)
            .where(Listing.store_id == store.id)
        )
    ).one()
    job = await store_sync.sync_job(db, store)
    state = (
        StoreState.SYNCED
        if store.imported_at is not None
        else StoreState.SYNCING
        if store_sync.syncing(job)
        else StoreState.PENDING
    )
    return StoreRead.model_validate(
        {
            **store.model_dump(),
            "state": state,
            "sync_job_id": None if job is None else job.id,
            "locations": [location.model_dump() for location in located.all()],
            "listings": total,
            "linked": linked,
            "pushes_queued": queued,
            "pushes_failed": failed,
        }
    )


async def _queue_store(db: WriteSession, store: Store) -> None:
    """Queue what is available of each item the store's listings sell."""
    sold = await db.exec(
        select(Listing.item_id)
        .where(Listing.store_id == store.id, col(Listing.item_id).is_not(None))
        .distinct()
    )
    await push.queue(
        db, [item_id for item_id in sold.all() if item_id is not None], store_id=store.id
    )


@router.get("", responses=_READ)
async def list_stores(db: OrganizationSessionDep) -> list[StoreRead]:
    """The community's stores, one per channel, connected or once connected."""
    found_stores = await db.exec(select(Store).order_by(col(Store.channel)))
    return [await _store_read(db, store) for store in found_stores.all()]


@router.post("/{store_id}/refresh", responses=_ADMIN)
async def refresh_store(
    store_id: int, db: WriteSessionDep, plugin: PluginDep, session: AdminDep
) -> StoreRead:
    """Read a synced store's locations and products again, taking each
    product not yet listed into Hoard as its sync did, and push each listing
    what is available."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    connector = await store_connector(plugin, session.community_ref, store)
    try:
        locations = await connector.locations()
        products = [product async for product in connector.products()]
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    await listings.keep_locations(db, store, locations)
    await listings.adopt(db, store, products)
    await _queue_store(db, store)
    await db.commit()
    return await _store_read(db, store)


@router.get("/{store_id}/store-locations", responses={**_LIVE, 403: {"description": "ADMIN_ONLY"}})
async def read_store_locations(
    store_id: int, db: OrganizationSessionDep, plugin: PluginDep, session: AdminDep
) -> list[StoreLocationFound]:
    """The store's locations, read from the store now, for choosing where
    each one's stock goes before a sync."""
    store = await found(db, Store, store_id)
    connector = await store_connector(plugin, session.community_ref, store)
    try:
        locations = await connector.locations()
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    return [
        StoreLocationFound(external_id=external_id, name=name)
        for external_id, name in locations.items()
    ]


@router.post(
    "/{store_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_READ,
        403: {"description": "ADMIN_ONLY, READ_ONLY"},
        409: {"description": "STORE_NOT_CONNECTED, STORE_SYNCED"},
        422: {"description": "LOCATION_UNKNOWN, STORE_FIELD_TARGET_INVALID"},
    },
)
async def sync_store(
    store_id: int,
    body: StoreSyncWrite,
    db: WriteSessionDep,
    session: AdminDep,
    address: WebhookAddressDep,
) -> JobRead:
    """Queue the store's sync, taking each store location's stock as
    ``body`` says. A sync already waiting or running is answered instead."""
    store = await found(db, Store, store_id)
    if not store.connected:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_NOT_CONNECTED)
    if store.imported_at is not None:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_SYNCED)
    for choice in body.locations:
        if (
            not choice.new
            and choice.location_id is not None
            and await db.get(Location, choice.location_id) is None
        ):
            raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, StoreError.LOCATION_UNKNOWN)
    choices = [
        store_sync.LocationChoice(choice.external_id, choice.location_id, choice.new)
        for choice in body.locations
    ]
    fields = [choice.choice() for choice in body.fields]
    for choice in fields:
        if choice.target is FieldTarget.PROPERTY and choice.property_id is not None:
            definition = await db.get(PropertyDefinition, choice.property_id)
            if definition is None or definition.kind is not RecordKind.PRODUCT:
                raise refuse(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    store_fields.StoreFieldError.STORE_FIELD_TARGET_INVALID,
                )
    job = await jobs.enqueue(
        db,
        store_sync.STORE_SYNC_JOB,
        store_sync.job_args(store, address, choices, fields),
        started_by=session.member_ref,
        unique_key=str(store.id),
    )
    await db.commit()
    return JobRead.model_validate(job, from_attributes=True)


@router.post(
    "/{store_id}/unsync",
    responses={**_ADMIN, 409: {"description": "STORE_SYNCING"}},
)
async def unsync_store(
    store_id: int,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: AdminDep,
    address: WebhookAddressDep,
) -> StoreRead:
    """Remove the store's sync: Hoard stops hearing from it and pushing to it,
    and it is pending again. Everything the sync brought in stays."""
    store = await found(db, Store, store_id)
    if store_sync.syncing(await store_sync.sync_job(db, store)):
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_SYNCING)
    connector = None
    if store.connected:
        try:
            connector = await channels.connect(plugin, session.community_ref, store.channel)
        except (InitiativeUnavailable, InitiativeRefused):
            raise refuse(
                status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE
            ) from None
        except ConnectRefused:
            connector = None
    await store_sync.unsync(db, store, connector, address)
    await db.commit()
    return await _store_read(db, store)


@router.put(
    "/{store_id}/locations/{store_location_id}",
    responses={
        **_READ,
        403: {"description": "ADMIN_ONLY, READ_ONLY"},
        422: {"description": "LOCATION_UNKNOWN"},
    },
)
async def map_store_location(
    store_id: int,
    store_location_id: int,
    body: StoreLocationWrite,
    db: WriteSessionDep,
    _admin: AdminDep,
) -> StoreLocationRead:
    """Set the Hoard location whose stock a store location sells, or none; the
    store is then pushed what is available there."""
    store = await found(db, Store, store_id)
    store_location = await found(db, StoreLocation, store_location_id)
    if store_location.store_id != store.id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if body.location_id is not None and await db.get(Location, body.location_id) is None:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, StoreError.LOCATION_UNKNOWN)
    store_location.location_id = body.location_id
    db.add(store_location)
    await db.flush()
    await _queue_store(db, store)
    await db.commit()
    return StoreLocationRead.model_validate(store_location.model_dump())


@router.get("/{store_id}/pushes", responses=_READ)
async def list_store_pushes(
    store_id: int,
    db: OrganizationSessionDep,
    page: PageDep,
    status: PushStatus | None = None,
) -> Page[StorePushRead]:
    """The store's push log, newest first: every push, or those standing as asked."""
    await found(db, Store, store_id)
    query = (
        select(StockPush, Listing, StoreLocation)
        .join(Listing, col(Listing.id) == StockPush.listing_id)
        .outerjoin(StoreLocation, col(StoreLocation.id) == StockPush.store_location_id)
        .where(Listing.store_id == store_id)
    )
    if status is not None:
        query = query.where(StockPush.status == status)
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    rows = await db.exec(
        query.order_by(col(StockPush.created_at).desc(), col(StockPush.id).desc())
        .offset(page.offset)
        .limit(page.limit)
    )
    return Page(
        items=[
            StorePushRead.model_validate(
                {
                    **stock_push.model_dump(),
                    "title": listing.title,
                    "sku": listing.sku,
                    "location": None if store_location is None else store_location.name,
                }
            )
            for stock_push, listing, store_location in rows.all()
        ],
        total=total,
    )


async def _product_read(
    db: AsyncSession, store: Store, copy: StoreProductCopy, sees_costs: bool
) -> StoreProductRead:
    held = list(
        (
            await db.exec(
                select(Listing, Store)
                .join(Store, col(Store.id) == Listing.store_id)
                .where(Listing.store_id == store.id, Listing.product_id == copy.product_id)
                .order_by(col(Listing.id))
            )
        ).all()
    )
    by_variant = {listing.variant_id: listing for listing, _ in held}
    variants = []
    for variant in copy.variants:
        listing = by_variant.get(variant.get("id"))
        variants.append(
            StoreVariantRead(
                id=variant.get("id"),
                title=str(variant.get("title") or ""),
                sku=None if listing is None else listing.sku,
                price=listing.price if listing is not None and sees_costs else None,
                options=dict(variant.get("options") or {}),
            )
        )
    return StoreProductRead(
        store_id=store.id,
        id=copy.product_id,
        title=copy.title,
        description=copy.description,
        status=copy.status,
        options=list(copy.options),
        variants=variants,
        images=[
            StoreImageRead(id=image.get("id"), url=str(image["url"]), alt=image.get("alt"))
            for image in copy.images
        ],
        published_on=list(copy.published_on),
        product_type=copy.product_type,
        vendor=copy.vendor,
        tags=list(copy.tags),
        updated_at=copy.store_updated_at,
        listings=await reads(db, held),
    )


async def _copy(db: AsyncSession, store: Store, product_id: str) -> StoreProductCopy:
    """Hoard's copy of the store's product, or 404 ``NOT_FOUND``."""
    found_copy = await db.exec(
        select(StoreProductCopy).where(
            StoreProductCopy.store_id == store.id, StoreProductCopy.product_id == product_id
        )
    )
    copy = found_copy.first()
    if copy is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return copy


async def started_from(
    db: WriteSession,
    store: Store,
    connector: Connector,
    product_id: str,
    updated_at: datetime | None,
) -> StoreProduct:
    """The store's product as the store holds it now, when that is the version
    an edit started from, by the store's ``updated_at``. Otherwise the store's
    version is kept as Hoard's copy and the edit is refused with
    ``STORE_PRODUCT_CHANGED``; nothing is sent to the store."""
    try:
        product = await connector.product(product_id)
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    if product is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if product.updated_at != updated_at:
        await listings.match(db, store, [product])
        await db.commit()
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_PRODUCT_CHANGED)
    return product


@router.get("/{store_id}/products/{product_id}", responses=_READ)
async def read_store_product(
    store_id: int, product_id: str, db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> StoreProductRead:
    """A store's product, from Hoard's copy of it."""
    store = await found(db, Store, store_id)
    return await _product_read(db, store, await _copy(db, store, product_id), sees_costs)


def _edited(product: StoreProduct, body: StoreProductWrite, sees_costs: bool) -> StoreProduct:
    edits = {variant.id: variant for variant in body.variants}

    def edited(variant: StoreVariant) -> StoreVariant:
        edit = edits.get(variant.id or "")
        if edit is None:
            return variant
        return replace(variant, sku=edit.sku, price=edit.price if sees_costs else variant.price)

    return replace(
        product,
        title=body.title,
        description=body.description,
        status=body.status if body.status is not None else product.status,
        variants=[edited(variant) for variant in product.variants],
        images=[StoreImage(url=image.url, alt=image.alt, id=image.id) for image in body.images],
        published_on=list(body.published_on),
    )


@router.put("/{store_id}/products/{product_id}", responses=_CHANGE_LIVE)
async def save_store_product(
    store_id: int,
    product_id: str,
    body: StoreProductWrite,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
) -> StoreProductRead:
    """Write a product's edits to the store, while the store holds the version
    they started from, and answer it as the store keeps it now, kept as
    Hoard's copy. Its listings are kept, and those not yet linked matched
    again."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    connector = await store_connector(plugin, session.community_ref, store)
    product = await started_from(db, store, connector, product_id, body.updated_at)
    try:
        saved = await connector.save_product(_edited(product, body, sees_costs))
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    await listings.match(db, store, [saved])
    await db.commit()
    return await _product_read(db, store, await _copy(db, store, product_id), sees_costs)


def _new_product(product: Product, variations: list[Variation], sees_costs: bool) -> StoreProduct:
    options: list[str] = []
    for variation in variations:
        options.extend(name for name in variation.attributes if name not in options)
    return StoreProduct(
        id=None,
        title=product.name,
        status="draft",
        options=options,
        variants=[
            StoreVariant(
                id=None,
                title=" / ".join(variation.attributes.values()) or product.name,
                sku=variation.sku,
                price=(variation.price or product.default_price) if sees_costs else None,
                options=dict(variation.attributes),
            )
            for variation in variations
        ],
    )


@router.post("/{store_id}/products", status_code=status.HTTP_201_CREATED, responses=_CHANGE_LIVE)
async def create_store_product(
    store_id: int,
    body: StoreProductCreate,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
) -> StoreProductRead:
    """Make a Hoard product in the store, as a draft with a variant for each of
    its variations, each listing linked to its variation at once."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    product = await found(db, Product, body.product_id)
    variations = list(
        (
            await db.exec(
                select(Variation)
                .where(Variation.product_id == product.id)
                .order_by(col(Variation.is_default).desc(), col(Variation.id))
            )
        ).all()
    )
    connector = await store_connector(plugin, session.community_ref, store)
    try:
        made = await connector.create_product(_new_product(product, variations, sees_costs))
    except ConnectRefused as refused:
        raise store_refused(refused) from None
    kept = await listings.match(db, store, [made])
    for listing, variation in zip(kept, variations, strict=False):
        listing.item_id, listing.matched_by = variation.id, MatchedBy.EXTERNAL_ID
        db.add(listing)
    await db.flush()
    await push.queue(db, [variation.id for variation in variations], store_id=store.id)
    await db.commit()
    assert made.id is not None
    return await _product_read(db, store, await _copy(db, store, made.id), sees_costs)


# Fields.


def _target(target: FieldTarget, property_id: int | None, pairs: dict) -> StoreFieldTargetRead:
    return StoreFieldTargetRead(target=target, property_id=property_id, pairs=pairs)


async def _found_fields(connector: Connector) -> list[StoreField]:
    try:
        return await connector.fields()
    except ConnectRefused as refused:
        raise store_refused(refused) from None


@router.get("/{store_id}/fields", responses={**_LIVE, 403: {"description": "ADMIN_ONLY"}})
async def list_store_fields(
    store_id: int, db: OrganizationSessionDep, plugin: PluginDep, session: AdminDep
) -> list[StoreFieldRead]:
    """The fields the store defines for its products and variants, read from
    the store now, each with what Hoard suggests it fill and what it fills."""
    store = await found(db, Store, store_id)
    found_fields = await _found_fields(await store_connector(plugin, session.community_ref, store))
    properties = list(
        (
            await db.exec(
                select(PropertyDefinition)
                .where(PropertyDefinition.kind == RecordKind.PRODUCT)
                .order_by(col(PropertyDefinition.id))
            )
        ).all()
    )
    mapped = {
        (mapping.owner, mapping.handle): mapping
        for mapping in await store_fields.mappings(db, store)
    }
    read = []
    for each in found_fields:
        suggested = store_fields.suggest(each, properties)
        mapping = mapped.get((each.owner, each.handle))
        read.append(
            StoreFieldRead(
                owner=each.owner,
                handle=each.handle,
                name=each.name,
                type=each.type,
                count=each.count,
                entries=[
                    StoreFieldEntry(value=value, name=name) for value, name in each.choices.items()
                ],
                property_type=store_fields.property_type(each),
                allergens=store_fields.allows(each, FieldTarget.ALLERGENS),
                ingredients=store_fields.allows(each, FieldTarget.INGREDIENTS),
                suggested=None
                if suggested is None
                else _target(suggested.target, suggested.property_id, dict(suggested.pairs)),
                mapping_id=None if mapping is None else mapping.id,
                mapped=None
                if mapping is None
                else _target(mapping.target, mapping.property_id, dict(mapping.pairs)),
            )
        )
    return read


@router.put("/{store_id}/fields", responses={**_ADMIN, 422: {"description": "STORE_FIELD_*"}})
async def map_store_field(
    store_id: int,
    body: StoreFieldChoiceWrite,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: AdminDep,
) -> JobRead:
    """Map one of a synced store's fields to what it fills, making the new
    property it asks for, and read its values in, as the sync does, as a job."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    found_fields = await _found_fields(await store_connector(plugin, session.community_ref, store))
    try:
        mapping = await store_fields.keep(db, store, found_fields, body.choice())
    except StoreFieldRefused as refused:
        raise _field_refused(refused) from None
    assert mapping is not None
    job = await store_fields.queue_read(db, store, mapping, session.member_ref)
    await db.commit()
    return JobRead.model_validate(job, from_attributes=True)


@router.delete(
    "/{store_id}/fields/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_READ, 403: {"description": "ADMIN_ONLY, READ_ONLY"}},
)
async def unmap_store_field(
    store_id: int, mapping_id: int, db: WriteSessionDep, _admin: AdminDep
) -> None:
    """Leave one of the store's fields out: it is read and written no more.
    What it filled stays."""
    mapping = await found(db, StoreFieldMapping, mapping_id)
    if mapping.store_id != store_id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    await db.delete(mapping)
    await db.commit()


@router.get("/{store_id}/fields/differing", responses=_READ)
async def list_store_field_differences(
    store_id: int, db: OrganizationSessionDep
) -> list[StoreFieldDifferenceRead]:
    """The products whose allergens or ingredient list, as Hoard works them
    out, differ from what the store holds."""
    store = await found(db, Store, store_id)
    return [
        StoreFieldDifferenceRead(
            mapping_id=difference.mapping.id,
            field=difference.mapping.name,
            target=difference.mapping.target,
            owner_id=difference.owner_id,
            product_id=None if difference.product is None else difference.product.id,
            product_name=None if difference.product is None else difference.product.name,
            hoard=difference.hoard,
            store=difference.store,
        )
        for difference in await store_fields.differing(db, store)
    ]


@router.post(
    "/{store_id}/fields/send",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_READ,
        403: {"description": "ADMIN_ONLY, READ_ONLY"},
        409: {"description": "STORE_NOT_SYNCED"},
    },
)
async def send_store_fields(store_id: int, db: WriteSessionDep, session: AdminDep) -> JobRead:
    """Send Hoard's allergens and ingredient lists to the store wherever they
    differ from the store's, as a job."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    job = await store_fields.queue_send(db, store, session.member_ref)
    await db.commit()
    return JobRead.model_validate(job, from_attributes=True)
