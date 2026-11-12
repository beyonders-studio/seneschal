"""Listings: each store product or variant and the item it sells, matched or
linked by hand (``hoard.listings``)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import listings
from hoard.api.settings import found
from hoard.api.shapes import Page, PageDep
from hoard.records import (
    Channel,
    ItemKind,
    Listing,
    MatchedBy,
    Material,
    Product,
    Store,
    Variation,
)
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/listings", tags=["listings"])

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}


class ListingError(StrEnum):
    """Why a listing was not linked."""

    #: The item is not one a store sells: a material or a product's variation.
    ITEM_NOT_SOLD = "ITEM_NOT_SOLD"


class ListingRead(BaseModel):
    id: int
    store_id: int
    channel: Channel
    #: The store's ids for its product, its variant and the inventory item.
    product_id: str
    variant_id: str | None
    inventory_item_id: str | None
    sku: str | None
    title: str | None
    #: The item it sells, none until it is matched or linked.
    item_id: int | None
    #: What the item is called, and what it is.
    item_name: str | None
    item_kind: ItemKind | None
    #: The product a variation it sells belongs to, for its page.
    item_product_id: int | None
    matched_by: MatchedBy | None


class ListingLink(BaseModel):
    """The item a listing sells, or null to unlink it."""

    item_id: int | None


async def _named(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """What each item is called, what kind it is, and its variation's product."""
    ids = set(item_ids)
    if not ids:
        return {}
    named: dict[int, dict[str, Any]] = {}
    materials = await db.exec(select(Material.id, Material.name).where(col(Material.id).in_(ids)))
    for item_id, name in materials.all():
        named[item_id] = {
            "item_name": name,
            "item_kind": ItemKind.MATERIAL,
            "item_product_id": None,
        }
    variations = await db.exec(
        select(Variation, Product)
        .join(Product, col(Product.id) == Variation.product_id)
        .where(col(Variation.id).in_(ids))
    )
    for variation, product in variations.all():
        named[variation.id] = {
            "item_name": " ".join([product.name, *variation.attributes.values()]),
            "item_kind": ItemKind.VARIATION,
            "item_product_id": product.id,
        }
    return named


async def reads(db: AsyncSession, rows: Iterable[tuple[Listing, Store]]) -> list[ListingRead]:
    """Each listing, with its store's channel and its item's name."""
    rows = list(rows)
    named = await _named(db, [listing.item_id for listing, _ in rows if listing.item_id])
    nothing = {"item_name": None, "item_kind": None, "item_product_id": None}
    return [
        ListingRead.model_validate(
            {
                **listing.model_dump(),
                "channel": store.channel,
                **(named.get(listing.item_id, nothing) if listing.item_id else nothing),
            }
        )
        for listing, store in rows
    ]


@router.get("", responses=_READ)
async def list_listings(
    db: OrganizationSessionDep,
    page: PageDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    store_id: int | None = None,
    linked: bool | None = None,
    product_id: Annotated[
        int | None, Query(description="Only those selling one of this product's variations.")
    ] = None,
) -> Page[ListingRead]:
    """The stores' listings, by title: those whose title or SKU holds ``q``,
    of one store, linked or not, or selling one product, as asked."""
    query = select(Listing, Store).join(Store, col(Store.id) == Listing.store_id)
    if q:
        query = query.where(
            or_(
                col(Listing.title).icontains(q, autoescape=True),
                col(Listing.sku).icontains(q, autoescape=True),
            )
        )
    if store_id is not None:
        query = query.where(Listing.store_id == store_id)
    if linked is not None:
        query = query.where(
            col(Listing.item_id).is_not(None) if linked else col(Listing.item_id).is_(None)
        )
    if product_id is not None:
        query = query.where(
            col(Listing.item_id).in_(select(Variation.id).where(Variation.product_id == product_id))
        )
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    rows = await db.exec(
        query.order_by(col(Listing.title), col(Listing.id)).offset(page.offset).limit(page.limit)
    )
    return Page(items=await reads(db, rows.all()), total=total)


@router.put(
    "/{listing_id}",
    responses={**_READ, 403: {"description": "READ_ONLY"}, 422: {"description": "ITEM_NOT_SOLD"}},
)
async def link_listing(listing_id: int, body: ListingLink, db: WriteSessionDep) -> ListingRead:
    """Link a listing to the item it sells by hand, or unlink it; its store is
    then pushed what is available of the item."""
    listing = await found(db, Listing, listing_id)
    try:
        await listings.link(db, listing, body.item_id)
    except LookupError:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, ListingError.ITEM_NOT_SOLD) from None
    await db.commit()
    store = await found(db, Store, listing.store_id)
    [read] = await reads(db, [(listing, store)])
    return read
