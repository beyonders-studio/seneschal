"""Listings: a store's products and variants, each linked to the item it
sells, by SKU, then title, then the store's id, or by hand; the store's
locations, each mapped to the Hoard location whose stock it sells; and noting
when an item's stock changes, so that each of its listings is pushed what is
available (``hoard.push``).

Matching links a listing only where one item answers:

* **SKU**: the one variation whose SKU is the listing's, ignoring case and
  the spaces around it.
* **Title**: the one active product named as the store's product, then its
  variation whose attributes' values are the variant's options' values; a
  store product with one variant takes a product with one variation.
* **The store's id**: the item another listing of the store sells under the
  same inventory item.

A listing linked by hand keeps its link until someone changes it, and no
listing loses its link when the store's product changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import delete, func
from sqlmodel import col, select

from hoard import push
from hoard.channels import StoreProduct, StoreVariant
from hoard.records import (
    Item,
    ItemKind,
    Listing,
    Location,
    MatchedBy,
    Product,
    ProductStatus,
    Store,
    StoreLocation,
    Variation,
)
from seneschal_core.db import WriteSession

#: The items a listing may sell: what is bought and resold, or made and sold.
SOLD_KINDS = frozenset({ItemKind.MATERIAL, ItemKind.VARIATION})


async def note_changed(db: WriteSession, item_id: int) -> None:
    """Note that an item's stock changed, so that each of its listings is
    pushed what is available once the change commits."""
    push.note(db, item_id)


def _key(text: str | None) -> str:
    return (text or "").strip().casefold()


def title_of(product: StoreProduct, variant: StoreVariant | None) -> str:
    """A listing's title: the store product's, with its variant's where the
    product has several."""
    if variant is None or len(product.variants) < 2 or not variant.title:
        return product.title
    return f"{product.title} - {variant.title}"


async def _by_sku(db: WriteSession, sku: str | None) -> int | None:
    if not _key(sku):
        return None
    found = await db.exec(
        select(Variation.id).where(func.lower(func.trim(Variation.sku)) == _key(sku)).limit(2)
    )
    ids = found.all()
    return ids[0] if len(ids) == 1 else None


async def _by_title(
    db: WriteSession, product: StoreProduct, variant: StoreVariant | None
) -> int | None:
    named = await db.exec(
        select(Product.id)
        .where(
            func.lower(func.trim(Product.name)) == _key(product.title),
            Product.status == ProductStatus.ACTIVE,
        )
        .limit(2)
    )
    products = named.all()
    if len(products) != 1:
        return None
    variations = (await db.exec(select(Variation).where(Variation.product_id == products[0]))).all()
    if len(variations) == 1 and len(product.variants) <= 1:
        return variations[0].id
    if variant is None:
        return None
    wanted = sorted(_key(value) for value in variant.options.values())
    fitting = [
        variation.id
        for variation in variations
        if sorted(_key(value) for value in variation.attributes.values()) == wanted
    ]
    return fitting[0] if len(fitting) == 1 else None


async def _by_store_id(db: WriteSession, store: Store, listing: Listing) -> int | None:
    if listing.inventory_item_id is None:
        return None
    found = await db.exec(
        select(Listing.item_id)
        .where(
            Listing.store_id == store.id,
            Listing.inventory_item_id == listing.inventory_item_id,
            col(Listing.item_id).is_not(None),
        )
        .distinct()
        .limit(2)
    )
    ids = found.all()
    return ids[0] if len(ids) == 1 else None


async def _matched(
    db: WriteSession,
    store: Store,
    listing: Listing,
    product: StoreProduct,
    variant: StoreVariant | None,
) -> tuple[int, MatchedBy] | None:
    if (item_id := await _by_sku(db, listing.sku)) is not None:
        return item_id, MatchedBy.SKU
    if (item_id := await _by_title(db, product, variant)) is not None:
        return item_id, MatchedBy.TITLE
    if (item_id := await _by_store_id(db, store, listing)) is not None:
        return item_id, MatchedBy.EXTERNAL_ID
    return None


async def match(db: WriteSession, store: Store, products: Iterable[StoreProduct]) -> list[Listing]:
    """Keep a listing for each of ``products``' variants, linking each not yet
    linked to an item by SKU, then title, then the store's id. A product's
    listings for variants it no longer has are removed. Answers the listings
    kept, in the products' order."""
    kept: list[Listing] = []
    for product in products:
        if product.id is None:
            continue
        found = await db.exec(
            select(Listing).where(Listing.store_id == store.id, Listing.product_id == product.id)
        )
        held = {listing.variant_id: listing for listing in found.all()}
        variants: list[StoreVariant | None] = [
            variant for variant in product.variants if variant.id is not None
        ]
        for variant in variants or [None]:
            variant_id = None if variant is None else variant.id
            listing = held.pop(variant_id, None) or Listing(
                organization_id=db.organization_id,
                store_id=store.id,
                product_id=product.id,
                variant_id=variant_id,
            )
            listing.sku = None if variant is None else variant.sku
            listing.inventory_item_id = None if variant is None else variant.inventory_item_id
            listing.title = title_of(product, variant)
            db.add(listing)
            await db.flush()
            if listing.item_id is None:
                matched = await _matched(db, store, listing, product, variant)
                if matched is not None:
                    listing.item_id, listing.matched_by = matched
                    await note_changed(db, listing.item_id)
            kept.append(listing)
        for gone in held.values():
            await db.delete(gone)
    await db.flush()
    return kept


async def forget(db: WriteSession, store: Store, product_id: str) -> None:
    """Remove the listings of a product the store no longer has."""
    await db.execute(
        delete(Listing).where(
            col(Listing.store_id) == store.id, col(Listing.product_id) == product_id
        )
    )


async def link(db: WriteSession, listing: Listing, item_id: int | None) -> Listing:
    """Link ``listing`` to ``item_id`` by hand, or unlink it for None. Raises
    :class:`LookupError` for an item a listing may not sell."""
    if item_id is not None:
        item = await db.get(Item, item_id)
        if item is None or item.kind not in SOLD_KINDS:
            raise LookupError(item_id)
    listing.item_id = item_id
    listing.matched_by = None if item_id is None else MatchedBy.HAND
    db.add(listing)
    await db.flush()
    if item_id is not None:
        await note_changed(db, item_id)
    return listing


async def keep_locations(
    db: WriteSession, store: Store, locations: Mapping[str, str]
) -> list[StoreLocation]:
    """Keep the store's ``locations``, each one's name by the store's id: new
    ones are added, renamed ones renamed, and those the store no longer has
    removed. While the organization has one location, a new store location
    sells its stock. Answers the store's locations, by name."""
    found = await db.exec(select(StoreLocation).where(StoreLocation.store_id == store.id))
    held = {location.external_id: location for location in found.all()}
    ours = (await db.exec(select(Location.id).limit(2))).all()
    only = ours[0] if len(ours) == 1 else None
    for external_id, name in locations.items():
        location = held.pop(external_id, None) or StoreLocation(
            organization_id=db.organization_id,
            store_id=store.id,
            external_id=external_id,
            location_id=only,
        )
        location.name = name
        db.add(location)
    for gone in held.values():
        await db.delete(gone)
    await db.flush()
    kept = await db.exec(
        select(StoreLocation)
        .where(StoreLocation.store_id == store.id)
        .order_by(col(StoreLocation.name), col(StoreLocation.id))
    )
    return list(kept.all())
