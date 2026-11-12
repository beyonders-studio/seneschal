"""Products, their variations and their images, and their stock per location.

A product sells through its variations, each an item with its own stock; a
simple product has one, its default. Prices and average costs are sent only to
a member who sees costs, and only theirs are taken. Images are kept by Hoard,
one of :data:`~hoard.records.IMAGE_TYPES` and at most
:data:`~hoard.records.MAX_IMAGE_BYTES` each.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import stock
from hoard.api import items
from hoard.api.deps import HoardError, SeesCostsDep
from hoard.api.properties import HoardPropertySummary, values_of
from hoard.api.settings import found, require_references
from hoard.api.shapes import Page, PageDep
from hoard.records import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    CategoryKind,
    Item,
    Product,
    ProductImage,
    ProductStatus,
    RecordKind,
    StockLevel,
    Variation,
)
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/products", tags=["products"])


class ProductError(StrEnum):
    """Why a change to a product was refused."""

    #: A product keeps at least one variation.
    LAST_VARIATION = "LAST_VARIATION"
    #: Not a picture Hoard keeps: a GIF, JPEG, PNG or WebP image.
    IMAGE_TYPE = "IMAGE_TYPE"
    #: Larger than a product image may be.
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"


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
    409: {"description": "LAST_VARIATION"},
    422: {"description": "CATEGORY_UNKNOWN"},
}


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


class ProductDetail(ProductRead, items.ItemStamps):
    """A product, with its variations' stock and lots at each location."""

    variations: list[ProductVariationRead]


class ProductWrite(BaseModel):
    """A product as it is added, or as an edit leaves it. Its default price is
    taken only from a member who sees costs."""

    name: items.Text
    status: ProductStatus = ProductStatus.ACTIVE
    category_id: int | None = None
    low_stock_limit: items.Amount | None = None
    default_price: items.Amount | None = None


class ProductVariationWrite(BaseModel):
    """A variation as it is added, or as an edit leaves it. Its price is taken
    only from a member who sees costs."""

    attributes: dict[items.Text, items.Text] = Field(default_factory=dict)
    sku: items.OptionalText = None
    price: items.Amount | None = None


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
    return ProductDetail.model_validate(
        {**_read(product, on_hand, sees_costs, values), "variations": variations}
    )


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
    product.sqlmodel_update(body.model_dump(exclude=_hidden(sees_costs, "default_price")))
    product, _ = await stock.add_product(db, product)
    await db.commit()
    return await _detail(db, product.id, sees_costs)


@router.put("/{product_id}", responses=_CHANGE)
async def update_product(
    product_id: int, body: ProductWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> ProductDetail:
    product = await found(db, Product, product_id)
    await require_references(db, kind=CategoryKind.PRODUCT, category_id=body.category_id)
    product.sqlmodel_update(body.model_dump(exclude=_hidden(sees_costs, "default_price")))
    db.add(product)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_product(product_id: int, db: WriteSessionDep) -> None:
    """Remove a product with its variations, their stock, lots and
    adjustments, and its images."""
    await stock.remove_product(db, await found(db, Product, product_id))
    await db.commit()


@router.post("/{product_id}/variations", status_code=status.HTTP_201_CREATED, responses=_CHANGE)
async def create_product_variation(
    product_id: int, body: ProductVariationWrite, db: WriteSessionDep, sees_costs: SeesCostsDep
) -> ProductDetail:
    await found(db, Product, product_id)
    variation = Variation(product_id=product_id)
    variation.sqlmodel_update(body.model_dump(exclude=_hidden(sees_costs, "price")))
    await stock.add_variation(db, variation)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.put("/{product_id}/variations/{variation_id}", responses=_CHANGE)
async def update_product_variation(
    product_id: int,
    variation_id: int,
    body: ProductVariationWrite,
    db: WriteSessionDep,
    sees_costs: SeesCostsDep,
) -> ProductDetail:
    variation = await _product_variation(db, product_id, variation_id)
    variation.sqlmodel_update(body.model_dump(exclude=_hidden(sees_costs, "price")))
    db.add(variation)
    await db.commit()
    return await _detail(db, product_id, sees_costs)


@router.delete(
    "/{product_id}/variations/{variation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_CHANGE,
)
async def delete_product_variation(product_id: int, variation_id: int, db: WriteSessionDep) -> None:
    """Remove a variation with its stock, lots and adjustments; a product
    keeps its last one."""
    await _product_variation(db, product_id, variation_id)
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
