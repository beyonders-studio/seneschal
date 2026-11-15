"""Products, their variations and their images, and their stock per location.

A product sells through its variations, each an item with its own stock; a
simple product has one, its default. Prices and average costs are sent only to
a member who sees costs, and only theirs are taken. Images are kept by Hoard,
one of :data:`~hoard.records.IMAGE_TYPES` and at most
:data:`~hoard.records.MAX_IMAGE_BYTES` each.

A product a synced store lists stays the store's to show: changing its name
or status, or a listed variation's SKU or options, is written to the store
first, from the version of the store's product the edit started from
(:func:`hoard.api.stores.started_from`), and the store's answer is kept as
Hoard's copy. Archiving it archives it in the store. It is not deleted while
the store lists it: it is archived, or deleted in the store first.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy import select as sa_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import listings, stock
from hoard.api import items, stores
from hoard.api.deps import HoardError, SeesCostsDep
from hoard.api.properties import HoardPropertySummary, values_of
from hoard.api.settings import found, require_references
from hoard.api.shapes import Page, PageDep
from hoard.channels import ConnectRefused, StoreProduct, StoreVariant
from hoard.records import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    AutoManufacture,
    CategoryKind,
    Channel,
    Item,
    Listing,
    Product,
    ProductImage,
    ProductStatus,
    RecordKind,
    StockLevel,
    Store,
    StoreProductCopy,
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

router = APIRouter(prefix="/products", tags=["products"])


class ProductError(StrEnum):
    """Why a change to a product was refused."""

    #: A product keeps at least one variation.
    LAST_VARIATION = "LAST_VARIATION"
    #: Not a picture Hoard keeps: a GIF, JPEG, PNG or WebP image.
    IMAGE_TYPE = "IMAGE_TYPE"
    #: Larger than a product image may be.
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    #: A synced store lists it: it is archived, or deleted in the store first.
    LISTED_IN_STORE = "LISTED_IN_STORE"


#: How each image type's content starts, but WebP's (:func:`_is_image`).
_SIGNATURES = {
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}

_READ: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    404: {"description": "NOT_FOUND"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "READ_ONLY"},
    409: {"description": "LAST_VARIATION, LISTED_IN_STORE"},
    422: {"description": "CATEGORY_UNKNOWN"},
}
#: A change written to the stores that list the product first.
_CHANGE_LIVE: dict[int | str, dict[str, Any]] = {
    **_CHANGE,
    409: {"description": "STORE_NOT_CONNECTED, STORE_PRODUCT_CHANGED"},
    502: {"description": "STORE_UNREACHABLE and the store's other refusals"},
    503: {"description": "INITIATIVE_UNAVAILABLE"},
}


class StoreVersion(BaseModel):
    """A version of a synced store's product: when the store last changed
    it, as Hoard's copy held it."""

    store_id: int
    #: The store's id for it.
    product_id: str = Field(min_length=1, max_length=200)
    updated_at: datetime | None


class ProductStoreProduct(StoreVersion):
    """A synced store's product that lists a product's variations."""

    channel: Channel


class ProductRead(BaseModel):
    id: int
    name: str
    status: ProductStatus
    category_id: int | None
    low_stock_limit: Decimal | None
    #: What is on hand of all its variations, at every location.
    on_hand: Decimal
    #: The price of a variation without its own; null without ``costs``.
    default_price: Decimal | None
    #: Whether a store order makes it; null takes the organization's.
    auto_manufacture: AutoManufacture | None
    #: How many days what a run makes cures before it is sold; null for none.
    cure_days: int | None
    #: Whether it is on the US Food Traceability List.
    traceability_list: bool
    #: Whether it is washed off after use, as an EU fragrance allergen's
    #: threshold asks.
    rinse_off: bool
    #: What it holds of the properties people add to products.
    properties: list[HoardPropertySummary]


class ProductVariationRead(items.ItemHoldings):
    id: int
    #: Each attribute's value, such as ``{"size": "large"}``; none for a
    #: simple product's default.
    attributes: dict[str, str]
    sku: str | None
    is_default: bool
    #: Its own price, when not the product's default; null without ``costs``.
    price: Decimal | None
    #: The moving average cost of one; null without ``costs``.
    average_cost: Decimal | None
    #: Whether a store order makes it; null takes its product's.
    auto_manufacture: AutoManufacture | None


class ProductDetail(ProductRead, items.ItemStamps):
    """A product, with its variations' stock and lots at each location."""

    variations: list[ProductVariationRead]
    #: The synced stores' products listing its variations, each as Hoard's
    #: copy holds it: an edit names these versions (``store_versions``).
    store_products: list[ProductStoreProduct]


class ProductWrite(BaseModel):
    """A product as it is added, or as an edit leaves it. Its default price is
    taken only from a member who sees costs."""

    name: items.Text
    status: ProductStatus = ProductStatus.ACTIVE
    category_id: int | None = None
    low_stock_limit: items.Amount | None = None
    default_price: items.Amount | None = None
    #: Left as it is unless sent; null takes the organization's.
    auto_manufacture: AutoManufacture | None = None
    cure_days: items.CureDays | None = None
    traceability_list: bool = False
    rinse_off: bool = False
    #: The versions of the store products listing it that an edit started
    #: from, as the product read them (``store_products``).
    store_versions: list[StoreVersion] = Field(default_factory=list, max_length=100)


class ProductVariationWrite(BaseModel):
    """A variation as it is added, or as an edit leaves it. Its price is taken
    only from a member who sees costs."""

    attributes: dict[items.Text, items.Text] = Field(default_factory=dict)
    sku: items.OptionalText = None
    price: items.Amount | None = None
    #: Left as it is unless sent; null takes its product's.
    auto_manufacture: AutoManufacture | None = None
    #: As for a product's (:attr:`ProductWrite.store_versions`).
    store_versions: list[StoreVersion] = Field(default_factory=list, max_length=100)


class ProductImageRead(BaseModel):
    id: int
    #: Its place among the product's images; the first shows for the product.
    position: int
    content_type: str
    #: Its size in bytes.
    size: int
    created_at: datetime
    created_by: str | None


def _hidden(sees_costs: bool, *fields: str) -> set[str]:
    """The fields a member who does not see costs neither sends nor takes."""
    return set() if sees_costs else set(fields)


def _written(body: ProductWrite | ProductVariationWrite, sees_costs: bool, price: str) -> dict:
    """What a write changes: its price only from a member who sees costs, and
    auto-manufacture only when it is sent."""
    unsent = {"auto_manufacture"} - body.model_fields_set
    return body.model_dump(exclude=_hidden(sees_costs, price) | unsent | {"store_versions"})


async def _on_hand(db: AsyncSession, product_ids: Sequence[int]) -> dict[int, Decimal]:
    totals = await db.exec(
        select(Variation.product_id, func.sum(StockLevel.quantity))
        .join(StockLevel, col(StockLevel.item_id) == col(Variation.id))
        .where(col(Variation.product_id).in_(product_ids))
        .group_by(col(Variation.product_id))
    )
    return {product_id: Decimal(total) for product_id, total in totals}


def _read(
    product: Product,
    on_hand: Decimal,
    sees_costs: bool,
    properties: list[HoardPropertySummary],
) -> dict[str, Any]:
    return {
        **product.model_dump(),
        "on_hand": on_hand,
        "properties": properties,
        **dict.fromkeys(_hidden(sees_costs, "default_price")),
    }


async def _detail(db: AsyncSession, product_id: int, sees_costs: bool) -> ProductDetail:
    """The product as the database holds it."""
    db.expunge_all()
    product = await found(db, Product, product_id)
    rows = (
        await db.exec(
            select(Variation, Item)
            .join(Item, col(Item.id) == col(Variation.id))
            .where(Variation.product_id == product_id)
            .order_by(col(Variation.is_default).desc(), col(Variation.id))
        )
    ).all()
    held = await items.holdings(db, [variation.id for variation, _ in rows])
    variations = [
        ProductVariationRead.model_validate(
            {
                **variation.model_dump(),
                **held[variation.id].model_dump(),
                "average_cost": item.average_cost,
                **dict.fromkeys(_hidden(sees_costs, "price", "average_cost")),
            }
        )
        for variation, item in rows
    ]
    on_hand = sum((variation.on_hand for variation in variations), Decimal(0))
    values = (await values_of(db, RecordKind.PRODUCT, [product_id]))[product_id]
    store_products = {
        (store.id, listing.product_id): ProductStoreProduct(
            store_id=store.id,
            channel=store.channel,
            product_id=listing.product_id,
            updated_at=None if copy is None else copy.store_updated_at,
        )
        for store, listing, copy in await _listed(db, product_id)
    }
    return ProductDetail.model_validate(
        {
            **_read(product, on_hand, sees_costs, values),
            "variations": variations,
            "store_products": list(store_products.values()),
        }
    )


async def _listed(
    db: AsyncSession, product_id: int, variation_id: int | None = None
) -> list[tuple[Store, Listing, StoreProductCopy | None]]:
    """The synced stores' listings selling a product's variations, or one of
    them, each with its store and Hoard's copy of its store product."""
    query = (
        select(Store, Listing, StoreProductCopy)
        .join(Listing, col(Listing.store_id) == col(Store.id))
        .join(Variation, col(Variation.id) == col(Listing.item_id))
        .outerjoin(
            StoreProductCopy,
            and_(
                col(StoreProductCopy.store_id) == col(Listing.store_id),
                col(StoreProductCopy.product_id) == col(Listing.product_id),
            ),
        )
        .where(Variation.product_id == product_id, col(Store.imported_at).is_not(None))
        .order_by(col(Listing.id))
    )
    if variation_id is not None:
        query = query.where(Variation.id == variation_id)
    return list((await db.exec(query)).all())


async def _write_through(
    db: WriteSession,
    plugin: Plugin,
    community_ref: str,
    listed: Sequence[tuple[Store, Listing, StoreProductCopy | None]],
    versions: Sequence[StoreVersion],
    edit: Callable[[StoreProduct], StoreProduct],
) -> None:
    """Write ``edit`` to each store product ``listed`` names. Each is read from
    its store first, and the edit is refused, with nothing sent, unless every
    one is the version it started from (``versions``). Each store's answer is
    kept as Hoard's copy."""
    started = {(version.store_id, version.product_id): version.updated_at for version in versions}
    stores_of = {(store.id, listing.product_id): store for store, listing, _ in listed}
    current = []
    for (store_id, product_id), store in stores_of.items():
        connector = await stores.store_connector(plugin, community_ref, store)
        product = await stores.started_from(
            db, store, connector, product_id, started.get((store_id, product_id))
        )
        current.append((store, connector, product))
    for store, connector, product in current:
        try:
            saved = await connector.save_product(edit(product))
        except ConnectRefused as refused:
            raise stores.store_refused(refused) from None
        await listings.match(db, store, [saved])


async def _refuse_listed(
    db: AsyncSession, product_id: int, variation_id: int | None = None
) -> None:
    if await _listed(db, product_id, variation_id):
        raise refuse(status.HTTP_409_CONFLICT, ProductError.LISTED_IN_STORE)


async def _product_variation(db: AsyncSession, product_id: int, variation_id: int) -> Variation:
    variation = await found(db, Variation, variation_id)
    if variation.product_id != product_id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return variation


@router.get("", responses=_READ)
async def list_products(
    db: OrganizationSessionDep,
    page: PageDep,
    sees_costs: SeesCostsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[ProductRead]:
    """Products by name: those whose name holds ``q``, when it is given."""
    query = select(Product)
    if q:
        query = query.where(col(Product.name).icontains(q, autoescape=True))
    total = (await db.exec(select(func.count()).select_from(query.subquery()))).one()
    products = (
        await db.exec(
            query.order_by(col(Product.name), col(Product.id)).offset(page.offset).limit(page.limit)
        )
    ).all()
    ids = [product.id for product in products]
    totals = await _on_hand(db, ids)
    values = await values_of(db, RecordKind.PRODUCT, ids)
    return Page(
        items=[
            ProductRead.model_validate(
                _read(product, totals.get(product.id, Decimal(0)), sees_costs, values[product.id])
            )
            for product in products
        ],
        total=total,
    )


@router.get("/{product_id}", responses=_READ)
async def read_product(
    product_id: int, db: OrganizationSessionDep, sees_costs: SeesCostsDep
) -> ProductDetail:
    """A product, with its variations' stock and lots at each location."""
    return await _detail(db, product_id, sees_costs)


@router.post("", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_product(
    body: ProductWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> ProductDetail:
    """Add a product with its default variation."""
    await require_references(db, kind=CategoryKind.PRODUCT, category_id=body.category_id)
    product = Product(name=body.name)
    product.sqlmodel_update(_written(body, sees_costs, "default_price"))
    product, _ = await stock.add_product(db, product)
    await db.commit()
    return await _detail(db, product.id, sees_costs)


@router.put("/{product_id}", responses=_CHANGE_LIVE)
async def update_product(
    product_id: int,
    body: ProductWrite,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
) -> ProductDetail:
    """Change a product. A new name or status reaches the synced stores
    listing it first: the name as their title, archiving as archiving."""
    product = await found(db, Product, product_id)
    await require_references(db, kind=CategoryKind.PRODUCT, category_id=body.category_id)
    renamed, restated = body.name != product.name, body.status != product.status
    if renamed or restated:

        def edit(held: StoreProduct) -> StoreProduct:
            return replace(
                held,
                title=body.name if renamed else held.title,
                status=body.status.value if restated else held.status,
            )

        listed = await _listed(db, product_id)
        await _write_through(db, plugin, session.community_ref, listed, body.store_versions, edit)
    product.sqlmodel_update(_written(body, sees_costs, "default_price"))
    db.add(product)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_product(product_id: int, db: WriteSessionDep) -> None:
    """Remove a product with its variations, their stock, lots and
    adjustments, and its images, unless a synced store lists it."""
    product = await found(db, Product, product_id)
    await _refuse_listed(db, product_id)
    await stock.remove_product(db, product)
    await db.commit()


@router.post("/{product_id}/variations", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_product_variation(
    product_id: int, body: ProductVariationWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> ProductDetail:
    await found(db, Product, product_id)
    variation = Variation(product_id=product_id)
    variation.sqlmodel_update(_written(body, sees_costs, "price"))
    await stock.add_variation(db, variation)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.put("/{product_id}/variations/{variation_id}", responses=_CHANGE_LIVE)
async def update_product_variation(
    product_id: int,
    variation_id: int,
    body: ProductVariationWrite,
    db: WriteSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    sees_costs: SeesCostsDep,
) -> ProductDetail:
    """Change a variation. A new SKU or new options reach the synced stores
    listing it first, as its variants' SKU and option values."""
    variation = await _product_variation(db, product_id, variation_id)
    new_sku, new_options = body.sku != variation.sku, body.attributes != variation.attributes
    if new_sku or new_options:
        listed = await _listed(db, product_id, variation_id)
        variants = {listing.variant_id for _, listing, _ in listed}

        def edited(held: StoreProduct, variant: StoreVariant) -> StoreVariant:
            if variant.id not in variants:
                return variant
            options = {
                name: value for name, value in body.attributes.items() if name in held.options
            }
            return replace(
                variant,
                sku=(body.sku or "") if new_sku else variant.sku,
                options={**variant.options, **options} if new_options else variant.options,
            )

        def edit(held: StoreProduct) -> StoreProduct:
            return replace(held, variants=[edited(held, variant) for variant in held.variants])

        await _write_through(db, plugin, session.community_ref, listed, body.store_versions, edit)
    variation.sqlmodel_update(_written(body, sees_costs, "price"))
    db.add(variation)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.delete(
    "/{product_id}/variations/{variation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_CHANGE,
)
async def delete_product_variation(product_id: int, variation_id: int, db: WriteSessionDep) -> None:
    """Remove a variation with its stock, lots and adjustments, unless a
    synced store lists it; a product keeps its last one."""
    await _product_variation(db, product_id, variation_id)
    await _refuse_listed(db, product_id, variation_id)
    count = select(func.count()).select_from(Variation).where(Variation.product_id == product_id)
    if (await db.exec(count)).one() == 1:
        raise refuse(status.HTTP_409_CONFLICT, ProductError.LAST_VARIATION)
    await stock.remove_item(db, variation_id)
    await db.commit()


#: A product image's description, without its content.
_IMAGES = sa_select(
    col(ProductImage.id),
    col(ProductImage.position),
    col(ProductImage.content_type),
    func.octet_length(col(ProductImage.content)).label("size"),
    col(ProductImage.created_at),
    col(ProductImage.created_by),
)


@router.get("/{product_id}/images", responses=_READ)
async def list_product_images(
    product_id: int, db: OrganizationSessionDep
) -> list[ProductImageRead]:
    """A product's images, first to last."""
    rows = await db.execute(
        _IMAGES.where(col(ProductImage.product_id) == product_id).order_by(
            col(ProductImage.position), col(ProductImage.id)
        )
    )
    return [ProductImageRead.model_validate(row, from_attributes=True) for row in rows]


def _is_image(content_type: str, content: bytes) -> bool:
    """Whether ``content`` starts as an image of ``content_type`` does."""
    if content_type == "image/webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return content.startswith(_SIGNATURES[content_type])


async def _image_content(request: Request) -> tuple[str, bytes]:
    """The image the request carries and its type, or a refusal."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in IMAGE_TYPES:
        raise refuse(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ProductError.IMAGE_TYPE)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise refuse(status.HTTP_413_CONTENT_TOO_LARGE, ProductError.IMAGE_TOO_LARGE)
    if not _is_image(content_type, bytes(content)):
        raise refuse(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ProductError.IMAGE_TYPE)
    return content_type, bytes(content)


@router.post(
    "/{product_id}/images",
    status_code=status.HTTP_201_CREATED,
    responses={
        **_READ,
        403: {"description": "READ_ONLY"},
        413: {"description": "IMAGE_TOO_LARGE"},
        415: {"description": "IMAGE_TYPE"},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def create_product_image(
    product_id: int, request: Request, db: WriteSessionDep
) -> ProductImageRead:
    """Add an image after a product's others. The body is the image itself,
    sent with its own type."""
    await found(db, Product, product_id)
    content_type, content = await _image_content(request)
    last = select(func.max(ProductImage.position)).where(ProductImage.product_id == product_id)
    position = (await db.exec(last)).one()
    image = ProductImage(
        organization_id=db.organization_id,
        product_id=product_id,
        position=0 if position is None else position + 1,
        content_type=content_type,
        content=content,
    )
    db.add(image)
    await db.commit()
    row = (await db.execute(_IMAGES.where(col(ProductImage.id) == image.id))).one()
    return ProductImageRead.model_validate(row, from_attributes=True)


async def _product_image(db: AsyncSession, product_id: int, image_id: int) -> ProductImage:
    image = await found(db, ProductImage, image_id)
    if image.product_id != product_id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return image


@router.get(
    "/{product_id}/images/{image_id}",
    response_class=Response,
    responses={**_READ, 200: {"description": "The image", "content": {"image/*": {}}}},
)
async def read_product_image(
    product_id: int, image_id: int, db: OrganizationSessionDep
) -> Response:
    """The image itself."""
    image = await _product_image(db, product_id, image_id)
    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/{product_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_READ, 403: {"description": "READ_ONLY"}},
)
async def delete_product_image(product_id: int, image_id: int, db: WriteSessionDep) -> None:
    await db.delete(await _product_image(db, product_id, image_id))
    await db.commit()
