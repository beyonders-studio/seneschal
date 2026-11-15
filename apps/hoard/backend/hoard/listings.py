"""Listings: a store's products and variants, each linked to the item it
sells, by SKU, then title, then the store's id, or by hand; Hoard's copy of
each store product; the store's locations, each mapped to the Hoard location
whose stock it sells; and noting when an item's stock changes, so that each
of its listings is pushed what is available (``hoard.push``).

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

Matching keeps the store product's copy (:class:`~hoard.records.StoreProductCopy`)
as the store answered it, each variant's price on its listing. A product read
as the store had it before the copy held, by the store's ``updated_at``,
changes nothing.

**Taking a store's products into Hoard** (:func:`adopt`), as a store's sync
does and as each product the store makes afterwards is taken: a product
whose listings are linked takes the store's title as its name, and each
variation a listing sells the variant's SKU and, where the product has
several variants, its option values as attributes. A listing still unlinked
is sold by a variation made for it: on the product another of its listings
sells, or on a product made with the store's title, active unless the store
archived it.

A store's locations start unmapped: a store's sync maps them as asked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import delete, func
from sqlmodel import col, select

from hoard import push, stock
from hoard.channels import StoreProduct, StoreVariant
from hoard.records import (
    Item,
    ItemKind,
    Listing,
    MatchedBy,
    Product,
    ProductStatus,
    Store,
    StoreLocation,
    StoreProductCopy,
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


async def _copy(db: WriteSession, store: Store, product_id: str) -> StoreProductCopy | None:
    found = await db.exec(
        select(StoreProductCopy).where(
            StoreProductCopy.store_id == store.id, StoreProductCopy.product_id == product_id
        )
    )
    return found.first()


def _older(product: StoreProduct, copy: StoreProductCopy | None) -> bool:
    """Whether ``product`` is the store's product as it was before ``copy``."""
    return (
        copy is not None
        and copy.store_updated_at is not None
        and product.updated_at is not None
        and product.updated_at < copy.store_updated_at
    )


def _keep_copy(
    db: WriteSession, store: Store, product: StoreProduct, copy: StoreProductCopy | None
) -> None:
    assert product.id is not None
    copy = copy or StoreProductCopy(
        organization_id=db.organization_id, store_id=store.id, product_id=product.id, title=""
    )
    copy.title = product.title
    copy.description = product.description
    copy.status = product.status
    copy.product_type = product.product_type
    copy.vendor = product.vendor
    copy.tags = list(product.tags)
    copy.options = list(product.options)
    copy.variants = [
        {"id": variant.id, "title": variant.title, "options": dict(variant.options)}
        for variant in product.variants
        if variant.id is not None
    ]
    copy.images = [{"id": image.id, "url": image.url, "alt": image.alt} for image in product.images]
    copy.published_on = list(product.published_on)
    copy.store_updated_at = product.updated_at
    db.add(copy)


async def _listings(db: WriteSession, store: Store, product_id: str) -> list[Listing]:
    found = await db.exec(
        select(Listing)
        .where(Listing.store_id == store.id, Listing.product_id == product_id)
        .order_by(col(Listing.id))
    )
    return list(found.all())


async def match(db: WriteSession, store: Store, products: Iterable[StoreProduct]) -> list[Listing]:
    """Keep a listing for each of ``products``' variants, linking each not yet
    linked to an item by SKU, then title, then the store's id, and keep each
    product's copy. A product's listings for variants it no longer has are
    removed. Answers the listings kept, in the products' order."""
    kept: list[Listing] = []
    for product in products:
        if product.id is None:
            continue
        held_listings = await _listings(db, store, product.id)
        copy = await _copy(db, store, product.id)
        if _older(product, copy):
            kept.extend(held_listings)
            continue
        held = {listing.variant_id: listing for listing in held_listings}
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
            listing.price = None if variant is None else variant.price
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
        _keep_copy(db, store, product, copy)
    await db.flush()
    return kept


async def _take(db: WriteSession, product: StoreProduct, held: list[Listing]) -> None:
    """Bring one store product into Hoard through its listings ``held``."""
    variants = {variant.id: variant for variant in product.variants if variant.id is not None}
    several = len(variants) > 1

    def fill(variation: Variation, listing: Listing) -> None:
        variant = variants.get(listing.variant_id)
        if variant is None:
            return
        if variant.sku:
            variation.sku = variant.sku
        if several:
            variation.attributes = dict(variant.options)
        db.add(variation)

    sold = [listing.item_id for listing in held if listing.item_id is not None]
    found = await db.exec(select(Variation).where(col(Variation.id).in_(sold)))
    variations = {variation.id: variation for variation in found.all()}
    owner: Product | None = None
    for product_id in sorted({variation.product_id for variation in variations.values()}):
        named = await db.get_one(Product, product_id)
        named.name = product.title
        db.add(named)
        owner = owner or named
    for listing in held:
        if listing.item_id in variations:
            fill(variations[listing.item_id], listing)
    spare: Variation | None = None
    for listing in held:
        if listing.item_id is not None:
            continue
        if owner is None:
            status = (
                ProductStatus.ARCHIVED if product.status == "archived" else ProductStatus.ACTIVE
            )
            owner, spare = await stock.add_product(db, Product(name=product.title, status=status))
        variation = spare or await stock.add_variation(db, Variation(product_id=owner.id))
        spare = None
        fill(variation, listing)
        listing.item_id, listing.matched_by = variation.id, MatchedBy.EXTERNAL_ID
        db.add(listing)
        await note_changed(db, variation.id)
    await db.flush()


async def adopt(
    db: WriteSession, store: Store, products: Iterable[StoreProduct], *, every: bool = False
) -> list[Listing]:
    """Keep ``products``' listings and copies as :func:`match` does, then take
    each product into Hoard: every one, or, unless ``every``, those the store
    had no listing for before. Answers the listings kept."""
    products = [product for product in products if product.id is not None]
    seen: set[str] = set()
    if not every:
        found = await db.exec(
            select(Listing.product_id)
            .where(
                Listing.store_id == store.id,
                col(Listing.product_id).in_([product.id for product in products]),
            )
            .distinct()
        )
        seen = set(found.all())
    kept = await match(db, store, products)
    for product in products:
        if product.id not in seen:
            held = [listing for listing in kept if listing.product_id == product.id]
            await _take(db, product, held)
    return kept


async def forget(db: WriteSession, store: Store, product_id: str) -> None:
    """Remove the listings, and the copy, of a product the store no longer
    has. What the listings sold stays in Hoard."""
    await db.execute(
        delete(Listing).where(
            col(Listing.store_id) == store.id, col(Listing.product_id) == product_id
        )
    )
    await db.execute(
        delete(StoreProductCopy).where(
            col(StoreProductCopy.store_id) == store.id,
            col(StoreProductCopy.product_id) == product_id,
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
    ones are added, unmapped, renamed ones renamed, and those the store no
    longer has removed. Answers the store's locations, by name."""
    found = await db.exec(select(StoreLocation).where(StoreLocation.store_id == store.id))
    held = {location.external_id: location for location in found.all()}
    for external_id, name in locations.items():
        location = held.pop(external_id, None) or StoreLocation(
            organization_id=db.organization_id, store_id=store.id, external_id=external_id
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
