"""The community's connected stores: each one's state, its locations mapped
to Hoard's, its push log, and its products, read and edited live.

A store's products are read from the store and written back to it as they
are edited, with no copy kept in Hoard (``hoard.channels``): their titles,
descriptions, images, prices and visibility are the store's. Prices are sent
and taken only for a member who sees costs. A Hoard product can be made in a
store, its listings linked to its variations at once.

Reading the store again and mapping its locations is an administrator's.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import channels, listings, push
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
    Variation,
)
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
_CHANGE_LIVE: dict[int | str, dict[str, Any]] = {**_LIVE, 403: {"description": "READ_ONLY"}}
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
    #: When its recent orders were copied in.
    imported_at: datetime | None
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
    """A store's product, as the store keeps it now."""

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
    return StoreRead.model_validate(
        {
            **store.model_dump(),
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
    """Read the store's locations and products again, matching each product
    not yet linked, and push each listing what is available."""
    store = await found(db, Store, store_id)
    connector = await _connector(plugin, session.community_ref, store)
    try:
        locations = await connector.locations()
        products = [product async for product in connector.products()]
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
    await listings.keep_locations(db, store, locations)
    await listings.match(db, store, products)
    await _queue_store(db, store)
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
    db: AsyncSession, store: Store, product: StoreProduct, sees_costs: bool
) -> StoreProductRead:
    assert product.id is not None
    held = await db.exec(
        select(Listing, Store)
        .join(Store, col(Store.id) == Listing.store_id)
        .where(Listing.store_id == store.id, Listing.product_id == product.id)
        .order_by(col(Listing.id))
    )
    return StoreProductRead(
        store_id=store.id,
        id=product.id,
        title=product.title,
        description=product.description,
        status=product.status,
        options=list(product.options),
        variants=[
            StoreVariantRead(
                id=variant.id,
                title=variant.title,
                sku=variant.sku,
                price=variant.price if sees_costs else None,
                options=dict(variant.options),
            )
            for variant in product.variants
        ],
        images=[
            StoreImageRead(id=image.id, url=image.url, alt=image.alt) for image in product.images
        ],
        published_on=list(product.published_on),
        listings=await reads(db, held.all()),
    )


@router.get("/{store_id}/products/{product_id}", responses=_LIVE)
async def read_store_product(
    store_id: int,
    product_id: str,
    db: OrganizationSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
) -> StoreProductRead:
    """A store's product, read from the store now."""
    store = await found(db, Store, store_id)
    connector = await _connector(plugin, session.community_ref, store)
    try:
        product = await connector.product(product_id)
    except ConnectRefused as refused:
        raise _store_refused(refused) from None
    if product is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return await _product_read(db, store, product, sees_costs)


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
    it now. Its listings are kept, and those not yet linked matched again."""
    store = await found(db, Store, store_id)
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
    return await _product_read(db, store, saved, sees_costs)


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
    return await _product_read(db, store, made, sees_costs)
