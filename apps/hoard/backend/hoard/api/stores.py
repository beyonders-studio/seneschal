"""The community's connected stores: each one's state, its sync, its
locations mapped to Hoard's, its push log, and its products.

A store is pending, syncing or synced (``hoard.store_sync``). Syncing it, and
removing its sync, is an administrator's, as are reading the store again and
mapping its locations.

A store's products are shown from Hoard's copy of them (``hoard.listings``),
which the store's webhooks and the reconcile keep up to date, and written
back to the store as they are edited; the store's answer is kept as the copy.
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

from hoard import channels, listings, push, store_sync
from hoard.api import items
from hoard.api.deps import AdminDep, HoardError, SeesCostsDep
from hoard.api.listings import ListingRead, reads
from hoard.api.settings import found
from hoard.api.shapes import Page, PageDep
from hoard.channels import Connector, ConnectRefused, StoreImage, StoreProduct, StoreVariant
from hoard.records import (
    Channel,
    Listing,
    Location,
    MatchedBy,
    Product,
    PushStatus,
    StockPush,
    Store,
    StoreLocation,
    StoreProductCopy,
    Variation,
)
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
    409: {"description": "STORE_NOT_CONNECTED, STORE_NOT_SYNCED"},
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


class StoreSyncWrite(BaseModel):
    """Where each of the store's locations takes its stock from; one not
    named takes none."""

    locations: list[StoreLocationChoice] = []


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

    title: items.Text
    description: str | None = Field(default=None, max_length=100_000)
    status: str | None = Field(default=None, max_length=50)
    variants: list[StoreVariantWrite] = []
    images: list[StoreImageWrite] = []
    published_on: list[str] = []


class StoreProductCreate(BaseModel):
    """The Hoard product to make in the store, with a variant per variation."""

    product_id: int


def _store_refused(refused: ConnectRefused) -> HTTPException:
    return refuse(status.HTTP_502_BAD_GATEWAY, refused.code)


def _require_synced(store: Store) -> None:
    """Refuse a write to a store that is not synced: Hoard writes nothing to it."""
    if store.imported_at is None:
        raise refuse(status.HTTP_409_CONFLICT, StoreError.STORE_NOT_SYNCED)


async def _connector(plugin: Plugin, community_ref: str, store: Store) -> Connector:
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
        raise _store_refused(refused) from None
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
    connector = await _connector(plugin, session.community_ref, store)
    try:
        locations = await connector.locations()
        products = [product async for product in connector.products()]
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
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
    connector = await _connector(plugin, session.community_ref, store)
    try:
        locations = await connector.locations()
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
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
        422: {"description": "LOCATION_UNKNOWN"},
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
    job = await jobs.enqueue(
        db,
        store_sync.STORE_SYNC_JOB,
        store_sync.job_args(store, address, choices),
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
    """Write a product's edits to the store, and answer it as the store keeps
    it now, kept as Hoard's copy. Its listings are kept, and those not yet
    linked matched again."""
    store = await found(db, Store, store_id)
    _require_synced(store)
    connector = await _connector(plugin, session.community_ref, store)
    try:
        product = await connector.product(product_id)
        if product is None:
            raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
        saved = await connector.save_product(_edited(product, body, sees_costs))
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
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
    connector = await _connector(plugin, session.community_ref, store)
    try:
        made = await connector.create_product(_new_product(product, variations, sees_costs))
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
    kept = await listings.match(db, store, [made])
    for listing, variation in zip(kept, variations, strict=False):
        listing.item_id, listing.matched_by = variation.id, MatchedBy.EXTERNAL_ID
        db.add(listing)
    await db.flush()
    await push.queue(db, [variation.id for variation in variations], store_id=store.id)
    await db.commit()
    assert made.id is not None
    return await _product_read(db, store, await _copy(db, store, made.id), sees_costs)
