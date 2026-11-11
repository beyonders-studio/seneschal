"""How each kind of record is listed and shown on its own page, in the UI
kit's layout shapes: a kind's table and its detail, each drawn as Hoard ships
it until someone changes it.

Every member reads a kind's layouts; only a community admin changes them
(:data:`~hoard.api.deps.AdminDep`). Hoard lists each kind in one table, and
draws on a detail its fields, its properties, its menu of actions and who made
and changed it; it has no plug-in's parts.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.api.deps import AdminDep
from hoard.records import Layout, LayoutView, RecordKind
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/layouts", tags=["layouts"])

#: The most parts one group or region holds, and columns one table shows.
_MOST = 200


class LayoutError(StrEnum):
    """Why a change to a layout was refused."""

    #: Hoard has no such layout of the kind: one table, and one detail.
    LAYOUT_UNKNOWN = "LAYOUT_UNKNOWN"


_READ: dict[int | str, dict[str, Any]] = {401: {"description": "NOT_AUTHENTICATED"}}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
    422: {"description": "LAYOUT_UNKNOWN"},
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _optional(**constraints: Any) -> Any:
    """A part's setting it may leave out, which a read then leaves out too."""
    return Field(default=None, exclude_if=lambda value: value is None, **constraints)


class HoardStackProps(_Strict):
    direction: Literal["column", "row"] | None = _optional()
    gap: Literal["xs", "sm"] | None = _optional()
    wrap: bool | None = _optional()
    #: Items keep their own width rather than filling the column.
    align: Literal["start"] | None = _optional()
    tone: Literal["muted"] | None = _optional()


class HoardSectionProps(_Strict):
    #: The organization's own words, drawn as written.
    title: str | None = _optional(max_length=100)
    #: Drawn folded until the reader opens it.
    collapsed: bool | None = _optional()


class HoardFieldProps(_Strict):
    #: One of the kind's fields, or ``property:<id>`` for one of its properties.
    field: str = Field(min_length=1, max_length=100)
    #: A person must fill it in.
    required: bool | None = _optional()


class HoardFieldPart(_Strict):
    type: Literal["field"]
    props: HoardFieldProps


class HoardPropertiesPart(_Strict):
    """Every property the record holds, but for those placed on their own."""

    type: Literal["properties"]


class HoardPluginPartProps(_Strict):
    plugin: int = Field(gt=0)
    part: str


class HoardPluginPart(_Strict):
    """A plug-in's part, as the UI kit's layouts may hold one. Hoard draws
    none, so a layout holding one is refused."""

    type: Literal["plugin"]
    props: HoardPluginPartProps

    @field_validator("type")
    @classmethod
    def _drawn(cls, value: str) -> str:
        raise ValueError("Hoard draws no plug-in's parts")


#: A detail's own parts, as the UI kit names them. Hoard draws its menu of
#: actions and who made and changed the record (``byline``).
HoardOwnPartType = Literal[
    "status",
    "dates",
    "byline",
    "notice",
    "actions",
    "relations",
    "case",
    "comments",
    "rsvp",
    "count",
    "body",
    "schedule",
    "reactions",
    "poll",
]
_OWN_PARTS = {"actions", "byline"}


class HoardOwnPartProps(_Strict):
    required: bool | None = _optional()


class HoardOwnPart(_Strict):
    type: HoardOwnPartType
    props: HoardOwnPartProps | None = _optional()

    @field_validator("type")
    @classmethod
    def _drawn(cls, value: str) -> str:
        if value not in _OWN_PARTS:
            raise ValueError("Hoard does not draw this part")
        return value


class HoardSectionPart(_Strict):
    """A bordered group of a detail's parts."""

    type: Literal["section"]
    props: HoardSectionProps | None = _optional()
    children: list[HoardDetailPart] = Field(default_factory=list, max_length=_MOST)


class HoardStackPart(_Strict):
    type: Literal["stack"]
    props: HoardStackProps | None = _optional()
    children: list[HoardDetailPart] = Field(default_factory=list, max_length=_MOST)


HoardDetailPart = Annotated[
    HoardStackPart
    | HoardSectionPart
    | HoardFieldPart
    | HoardPropertiesPart
    | HoardPluginPart
    | HoardOwnPart,
    Field(discriminator="type"),
]
HoardSectionPart.model_rebuild()
HoardStackPart.model_rebuild()

Region = Annotated[list[HoardDetailPart], Field(max_length=_MOST)]


class HoardDetailLayoutDefinition(_Strict):
    """A record's page in three regions, each its parts in order. A region it
    leaves out is drawn as shipped; what it places nowhere is not shown."""

    header: Region | None = None
    main: Region | None = None
    side: Region | None = None


class HoardListLayoutDefinition(_Strict):
    """How a table draws the records it lists: its columns, by field, and how
    it draws each scale or counter. What it leaves out is drawn as shipped."""

    columns: list[Annotated[str, Field(max_length=100)]] | None = Field(
        default=None, max_length=_MOST
    )
    displays: dict[str, Literal["number", "stars", "bar", "clock"]] | None = Field(
        default=None, max_length=_MOST
    )


#: Every way the UI kit lists records; Hoard draws a table.
HoardListKind = Literal["table", "board", "gantt", "calendar", "masonry", "grid", "timeline"]


class HoardListLayoutWrite(_Strict):
    kind: HoardListKind
    definition: HoardListLayoutDefinition


class HoardDetailLayoutWrite(_Strict):
    #: The kind of record whose page it lays out.
    kind: str
    definition: HoardDetailLayoutDefinition


class HoardListLayoutRead(BaseModel):
    kind: Literal["table"]
    #: The one table, which a kind's records open on.
    is_default: bool
    hidden: bool
    definition: HoardListLayoutDefinition
    #: Null while it is drawn as shipped.
    updated_at: datetime | None


class HoardDetailLayoutRead(BaseModel):
    #: The kind of record whose page it lays out.
    kind: RecordKind
    definition: HoardDetailLayoutDefinition
    #: Null while it is drawn as shipped.
    updated_at: datetime | None


class HoardLayoutSet(BaseModel):
    """A kind's table, then its detail."""

    layouts: list[HoardListLayoutRead | HoardDetailLayoutRead]


async def _set(db: AsyncSession, kind: RecordKind) -> HoardLayoutSet:
    db.expunge_all()
    stored = {
        layout.view: layout for layout in await db.exec(select(Layout).where(Layout.kind == kind))
    }
    table, detail = stored.get(LayoutView.TABLE), stored.get(LayoutView.DETAIL)
    return HoardLayoutSet(
        layouts=[
            HoardListLayoutRead(
                kind="table",
                is_default=True,
                hidden=False,
                definition=HoardListLayoutDefinition.model_validate(
                    table.definition if table else {}
                ),
                updated_at=table.updated_at if table else None,
            ),
            HoardDetailLayoutRead(
                kind=kind,
                definition=HoardDetailLayoutDefinition.model_validate(
                    detail.definition if detail else {}
                ),
                updated_at=detail.updated_at if detail else None,
            ),
        ]
    )


def _view(kind: RecordKind, layout: str) -> LayoutView:
    """Which of ``kind``'s layouts ``layout`` names: its table, or its detail
    by the kind's own name. Refuses (422) any other."""
    if layout == LayoutView.TABLE:
        return LayoutView.TABLE
    if layout == kind:
        return LayoutView.DETAIL
    raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, LayoutError.LAYOUT_UNKNOWN)


@router.get("/{kind}", responses=_READ)
async def read_layouts(kind: RecordKind, db: OrganizationSessionDep) -> HoardLayoutSet:
    """A kind's layouts, each as shipped until it is changed."""
    return await _set(db, kind)


@router.put("/{kind}", responses=_CHANGE)
async def save_layout(
    kind: RecordKind,
    body: HoardListLayoutWrite | HoardDetailLayoutWrite,
    _admin: AdminDep,
    db: WriteSessionDep,
) -> HoardLayoutSet:
    """Store one of a kind's layouts, answering with its layouts."""
    view = _view(kind, body.kind)
    if isinstance(body, HoardListLayoutWrite) != (view is LayoutView.TABLE):
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, LayoutError.LAYOUT_UNKNOWN)
    layout = (
        await db.exec(select(Layout).where(Layout.kind == kind, Layout.view == view))
    ).first() or Layout(organization_id=db.organization_id, kind=kind, view=view, definition={})
    layout.definition = body.definition.model_dump(exclude_none=True)
    db.add(layout)
    await db.commit()
    return await _set(db, kind)


@router.delete("/{kind}/{layout}", responses=_CHANGE)
async def reset_layout(
    kind: RecordKind, layout: str, _admin: AdminDep, db: WriteSessionDep
) -> HoardLayoutSet:
    """Draw one of a kind's layouts as shipped again, answering with its layouts."""
    view = _view(kind, layout)
    stored = (await db.exec(select(Layout).where(Layout.kind == kind, Layout.view == view))).first()
    if stored:
        await db.delete(stored)
        await db.commit()
    return await _set(db, kind)
