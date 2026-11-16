"""What the pages of materials, bases and products share: the fields they
take, who made a record and changed it, an item's stock, bin and lots at each
location, and the pictures Hoard keeps.

Under ``/items``, a material's or a base's bin at a location is set, and its
one picture kept: one of :data:`~hoard.records.IMAGE_TYPES` of at most
:data:`~hoard.records.MAX_IMAGE_BYTES`, as a product's images are. A picture
is never offered to a store.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func
from sqlalchemy import select as sa_select
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import summary, units
from hoard.api.adjustments import describe_items, location_or_main
from hoard.api.deps import HoardError
from hoard.api.properties import HoardPropertySummary, values_of
from hoard.api.settings import SettingsError, found
from hoard.holds import held_lots
from hoard.recipes import RecipeOutput
from hoard.records import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    Adjustment,
    AdjustmentKind,
    Bin,
    Image,
    Item,
    ItemKind,
    Location,
    Lot,
    ProductionRun,
    ProductionRunLine,
    PurchaseLine,
    Recipe,
    RecipeLine,
    RecordKind,
    StocktakeRow,
    Variation,
)
from hoard.stock import StockRefused, adjust
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse
from seneschal_core.db import WriteSession

router = APIRouter(prefix="/items", tags=["items"])


class ImageError(StrEnum):
    """Why a picture was refused."""

    #: Not a picture Hoard keeps: a GIF, JPEG, PNG or WebP image.
    IMAGE_TYPE = "IMAGE_TYPE"
    #: Larger than a picture may be.
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
_CHANGE: dict[int | str, dict[str, Any]] = {**_READ, 403: {"description": "READ_ONLY"}}
#: Taking an image: the request's body is the image itself, sent with its own type.
IMAGE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
IMAGE_REFUSALS: dict[int | str, dict[str, Any]] = {
    413: {"description": "IMAGE_TOO_LARGE"},
    415: {"description": "IMAGE_TYPE"},
}
#: How an image is served.
IMAGE_HEADERS = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}

#: A name or code someone types.
Text = Annotated[str, Field(min_length=1, max_length=200)]
OptionalText = Text | None
#: A quantity, cost or price: exact, to six places, and never below zero.
Amount = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=6)]
#: The same, above zero.
Positive = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=6)]
#: How many days what a run makes cures before it is used or sold.
CureDays = Annotated[int, Field(gt=0, le=3650)]
#: Words someone writes about a record.
Notes = Annotated[str, Field(max_length=2000)]


class ItemStamps(BaseModel):
    """Who made a record and when, and who last changed it and when: a
    member's reference, or null for Hoard itself."""

    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


class ItemBin(BaseModel):
    """Where an item sits at a location; any part may be left out."""

    zone: OptionalText = None
    section: OptionalText = None
    position: OptionalText = None


class ItemStockRead(BaseModel):
    """An item's stock at one location, and its bin there (``hoard.summary``)."""

    location_id: int
    #: What is on hand there.
    quantity: Decimal
    bin: ItemBin | None
    #: What its recipe could make there from what is usable; null with no
    #: recipe or no lines.
    could_make: Decimal | None
    #: The date of the latest stocktake that counted it there.
    last_stocktake: date | None


class ItemLotRead(BaseModel):
    """A lot of an item at a location, and what is left of it."""

    id: int
    location_id: int
    number: str
    dated_on: date
    expires_on: date | None
    notes: str | None
    balance: Decimal
    #: Whether it is on hold.
    on_hold: bool
    #: What it holds of the properties people add to lots.
    properties: list[HoardPropertySummary]


class ItemUsage(BaseModel):
    """What an item received by purchases and what production runs used of
    it, over one window ending today (``hoard.summary``)."""

    window: summary.UsageWindow
    received: Decimal
    used: Decimal


class ItemUsedIn(BaseModel):
    """A recipe holding an item, and what it makes: a base or a variation."""

    recipe_id: int
    output: RecipeOutput
    #: The base's or the variation's id.
    output_id: int
    #: The product a variation is of.
    product_id: int | None
    #: The base's name, or the variation's product's.
    name: str
    #: A variation's attributes; none for a base.
    attributes: dict[str, str]
    #: Whether the base or the variation's product is archived.
    archived: bool
    #: What one batch makes, in ``batch_unit_id``: the base's tracking unit;
    #: none for a variation, which counts in units of itself.
    batch_quantity: Decimal
    batch_unit_id: int | None
    #: How much one batch takes of the item, in ``unit_id``: the unit its line
    #: was written in, or the item's tracking unit; none for a variation.
    amount: Decimal
    unit_id: int | None


class ItemHoldings(BaseModel):
    """An item's figures (``hoard.summary``), its stock at each location it
    has stock at, was counted at, could make some at or has a bin at, and its
    lots."""

    on_hand: Decimal
    #: What open orders need of it and have not shipped; of a bundle's
    #: variation, how many bundles they need.
    committed: Decimal
    #: What is on hand less what is committed and on hold; of a bundle's
    #: variation, which holds no stock, how many its contents make together.
    available: Decimal
    #: What it could make at every location together; null with no recipe or
    #: no lines.
    potential: Decimal | None
    #: What its completed runs made.
    made: Decimal
    #: The date of the latest stocktake that counted it.
    last_stocktake: date | None
    #: What its purchase lines not yet received will bring, in its tracking unit.
    on_order: Decimal
    #: Whether a purchase bringing some of it is past the day it was expected.
    overdue: bool
    #: What it received and runs used over the last week, 1, 3, 6 and 12
    #: months, and all time, in that order.
    usage: list[ItemUsage]
    #: The recipes holding it, by what they make.
    used_in: list[ItemUsedIn]
    stock: list[ItemStockRead]
    lots: list[ItemLotRead]


async def on_hand(db: AsyncSession, item_ids: Iterable[int]) -> dict[int, Decimal]:
    """What is on hand of each item, at all its locations."""
    held = await summary.stock(db, item_ids)
    return {
        item_id: sum((at.on_hand for at in locations.values()), Decimal(0))
        for item_id, locations in held.items()
    }


async def holdings(
    db: AsyncSession, item_ids: Iterable[int], figures: dict[int, summary.Summary] | None = None
) -> dict[int, ItemHoldings]:
    """Each item's figures, as ``figures`` holds them when given, its stock
    and bin per location, and its lots, oldest first."""
    ids = list(item_ids)
    if figures is None:
        figures = await summary.summarise(db, ids)
    used_in = await _used_in(db, figures)
    held = {
        item_id: ItemHoldings(
            on_hand=figures[item_id].stock.on_hand,
            committed=figures[item_id].committed,
            available=figures[item_id].available,
            potential=figures[item_id].potential,
            made=figures[item_id].made,
            last_stocktake=figures[item_id].last_stocktake,
            on_order=figures[item_id].on_order.quantity,
            overdue=figures[item_id].on_order.overdue,
            usage=[
                ItemUsage.model_validate(usage, from_attributes=True)
                for usage in figures[item_id].usage
            ],
            used_in=used_in[item_id],
            stock=[],
            lots=[],
        )
        for item_id in ids
    }
    at: dict[tuple[int, int], ItemStockRead] = {
        (item_id, location_id): ItemStockRead(
            location_id=location_id,
            quantity=there.stock.on_hand,
            bin=None,
            could_make=there.could_make,
            last_stocktake=there.last_stocktake,
        )
        for item_id in ids
        for location_id, there in figures[item_id].locations.items()
    }
    bins = await db.exec(select(Bin).where(col(Bin.item_id).in_(ids)))
    for kept in bins:
        row = at.setdefault(
            (kept.item_id, kept.location_id),
            ItemStockRead(
                location_id=kept.location_id,
                quantity=Decimal(0),
                bin=None,
                could_make=None,
                last_stocktake=None,
            ),
        )
        row.bin = ItemBin.model_validate(kept, from_attributes=True)
    for (item_id, _), row in sorted(at.items()):
        held[item_id].stock.append(row)
    lots = (
        await db.exec(
            select(Lot, col(Lot.id).in_(held_lots()))
            .where(col(Lot.item_id).in_(ids))
            .order_by(col(Lot.dated_on), col(Lot.id))
        )
    ).all()
    values = await values_of(db, RecordKind.LOT, [lot.id for lot, _ in lots])
    for lot, on_hold in lots:
        held[lot.item_id].lots.append(lot_read(lot, on_hold, values[lot.id]))
    return held


async def _used_in(
    db: AsyncSession, figures: dict[int, summary.Summary]
) -> dict[int, list[ItemUsedIn]]:
    """The recipes holding each item, described by what they make, by its name."""
    outputs = {used.makes for summarised in figures.values() for used in summarised.used_in}
    described = await describe_items(db, outputs)
    products = dict(
        (
            await db.exec(
                select(Variation.id, Variation.product_id).where(col(Variation.id).in_(outputs))
            )
        ).all()
    )
    counted = await units.tracking_units(db, outputs)
    found = {
        item_id: [
            ItemUsedIn(
                recipe_id=used.recipe_id,
                output=RecipeOutput.VARIATION if used.makes in products else RecipeOutput.BASE,
                output_id=used.makes,
                product_id=products.get(used.makes),
                name=described[used.makes].name,
                attributes=described[used.makes].attributes,
                archived=described[used.makes].archived,
                batch_quantity=used.batch_quantity,
                batch_unit_id=counted.get(used.makes),
                amount=used.amount,
                unit_id=used.unit_id,
            )
            for used in summarised.used_in
        ]
        for item_id, summarised in figures.items()
    }
    for listed in found.values():
        listed.sort(key=lambda used: (used.name.casefold(), sorted(used.attributes.values())))
    return found


def lot_read(lot: Lot, on_hold: bool, properties: list[HoardPropertySummary]) -> ItemLotRead:
    return ItemLotRead.model_validate(
        {**lot.model_dump(), "on_hold": on_hold, "properties": properties}
    )


async def require_tracking_unit(db: AsyncSession, item_id: int, was: int, now: int) -> None:
    """Refuse (409 ``TRACKING_UNIT_IN_USE``) changing a material's or a base's
    tracking unit once anything records a number in it: its stock movements,
    purchases and stocktake counts, a recipe line naming it, its own recipe,
    or a run making or using it. Before then it changes freely."""
    if was == now:
        return
    for held in (
        select(Adjustment.id).where(Adjustment.item_id == item_id),
        select(PurchaseLine.id).where(PurchaseLine.item_id == item_id),
        select(StocktakeRow.id).where(StocktakeRow.item_id == item_id),
        select(RecipeLine.id).where(RecipeLine.item_id == item_id),
        select(Recipe.id).where(Recipe.base_id == item_id),
        select(ProductionRun.id).where(ProductionRun.item_id == item_id),
        select(ProductionRunLine.id).where(ProductionRunLine.item_id == item_id),
    ):
        if (await db.exec(held.limit(1))).first() is not None:
            raise refuse(status.HTTP_409_CONFLICT, SettingsError.TRACKING_UNIT_IN_USE)


async def _set_bin(db: WriteSession, item_id: int, location_id: int, bin: ItemBin) -> None:
    """Keep ``bin`` as the item's at the location; one with no part left removes it."""
    kept = (
        await db.exec(select(Bin).where(Bin.item_id == item_id, Bin.location_id == location_id))
    ).first()
    if not bin.model_dump(exclude_none=True):
        if kept is not None:
            await db.delete(kept)
        return
    kept = kept or Bin(organization_id=db.organization_id, item_id=item_id, location_id=location_id)
    kept.sqlmodel_update(bin.model_dump())
    db.add(kept)


class ItemStart(BaseModel):
    """Where a new material or base starts: at a location, the main one unless
    another is named, with its bin there and its starting stock."""

    location_id: int | None = None
    #: How much it starts with, which is its starting adjustment.
    quantity: Positive | None = None
    #: What one of it cost; taken only from a member who sees costs, and
    #: nothing otherwise.
    unit_cost: Amount | None = None
    bin: ItemBin | None = None


async def start(db: WriteSession, item_id: int, body: ItemStart, sees_costs: bool) -> None:
    """Give a new item its bin and starting stock, refusing (409
    ``NOT_INVENTORIABLE``) stock for a material that carries none."""
    location_id = await location_or_main(db, body.location_id)
    if body.bin is not None:
        await _set_bin(db, item_id, location_id, body.bin)
    if body.quantity is None:
        return
    adjustment = Adjustment(
        item_id=item_id,
        location_id=location_id,
        kind=AdjustmentKind.STARTING,
        dated_on=date.today(),
        change=body.quantity,
        unit_cost=(body.unit_cost or Decimal(0)) if sees_costs else Decimal(0),
    )
    try:
        await adjust(db, adjustment)
    except StockRefused as refused:
        raise refuse(status.HTTP_409_CONFLICT, refused.code) from None


@router.put(
    "/{item_id}/bins/{location_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE
)
async def set_item_bin(item_id: int, location_id: int, body: ItemBin, db: WriteSessionDep) -> None:
    """Set where an item sits at a location. One with no part removes it."""
    await found(db, Item, item_id)
    await found(db, Location, location_id)
    await _set_bin(db, item_id, location_id, body)
    await db.commit()


def _is_image(content_type: str, content: bytes) -> bool:
    """Whether ``content`` starts as an image of ``content_type`` does."""
    if content_type == "image/webp":
        return content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return content.startswith(_SIGNATURES[content_type])


async def image_content(request: Request) -> tuple[str, bytes]:
    """The image the request carries and its type, or a refusal."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in IMAGE_TYPES:
        raise refuse(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ImageError.IMAGE_TYPE)
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise refuse(status.HTTP_413_CONTENT_TOO_LARGE, ImageError.IMAGE_TOO_LARGE)
    if not _is_image(content_type, bytes(content)):
        raise refuse(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, ImageError.IMAGE_TYPE)
    return content_type, bytes(content)


#: An image's description, without its content.
IMAGES = sa_select(
    col(Image.id),
    col(Image.position),
    col(Image.content_type),
    func.octet_length(col(Image.content)).label("size"),
    col(Image.created_at),
    col(Image.created_by),
)


class ItemPictureRead(BaseModel):
    id: int
    content_type: str
    #: Its size in bytes.
    size: int
    created_at: datetime
    created_by: str | None


async def picture_id(db: AsyncSession, item_id: int) -> int | None:
    """The id of a material's or a base's picture, when it has one."""
    return (await db.exec(select(Image.id).where(Image.item_id == item_id))).first()


async def _pictured(db: AsyncSession, item_id: int) -> None:
    """Refuse (404) anything but a material or a base: a variation is
    pictured by its product's images."""
    item = await found(db, Item, item_id)
    if item.kind is ItemKind.VARIATION:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)


@router.put(
    "/{item_id}/picture",
    responses={**_CHANGE, **IMAGE_REFUSALS},
    openapi_extra=IMAGE_BODY,
)
async def set_item_picture(item_id: int, request: Request, db: WriteSessionDep) -> ItemPictureRead:
    """Picture a material or a base, in place of any picture it had. The body
    is the image itself, sent with its own type."""
    await _pictured(db, item_id)
    content_type, content = await image_content(request)
    await db.execute(delete(Image).where(col(Image.item_id) == item_id))
    image = Image(
        organization_id=db.organization_id,
        item_id=item_id,
        content_type=content_type,
        content=content,
    )
    db.add(image)
    await db.commit()
    row = (await db.execute(IMAGES.where(col(Image.id) == image.id))).one()
    return ItemPictureRead.model_validate(row, from_attributes=True)


@router.get(
    "/{item_id}/picture/{picture_id}",
    response_class=Response,
    responses={**_READ, 200: {"description": "The image", "content": {"image/*": {}}}},
)
async def read_item_picture(item_id: int, picture_id: int, db: OrganizationSessionDep) -> Response:
    """The picture itself."""
    image = await found(db, Image, picture_id)
    if image.item_id != item_id:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    return Response(content=image.content, media_type=image.content_type, headers=IMAGE_HEADERS)


@router.delete("/{item_id}/picture", status_code=status.HTTP_204_NO_CONTENT, responses=_CHANGE)
async def delete_item_picture(item_id: int, db: WriteSessionDep) -> None:
    await _pictured(db, item_id)
    await db.execute(delete(Image).where(col(Image.item_id) == item_id))
    await db.commit()
