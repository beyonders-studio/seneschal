"""Initiative files linked to lots, products, materials and bases
(``hoard.documents``), and the backup period the organization's rules ask for
(``hoard.backups``).

A linked file's name is read from Initiative acting for the member viewing
the page (``hoard.api.files``), so a file they may not open is listed without
it, and only a file they may open is linked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlmodel import SQLModel, select

from hoard import backups, documents
from hoard.api.adjustments import Responses
from hoard.api.files import MEMBER_READ, FileFound, file_found, read_for_member, unavailable
from hoard.api.settings import found
from hoard.records import Base, DocumentLink, Lot, Material, OrganizationSettings, Product, Regime
from seneschal_core.api import (
    OrganizationSessionDep,
    PluginDep,
    SessionDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.initiative_api import InitiativeRefused
from seneschal_core.sessions import SessionClaims

router = APIRouter(prefix="/documents", tags=["documents"])

#: The record each way of naming one is.
_RECORDS: dict[str, type[SQLModel]] = {
    "lot_id": Lot,
    "product_id": Product,
    "material_id": Material,
    "base_id": Base,
}


class DocumentError(StrEnum):
    """Why a document was refused."""

    #: A document is linked to exactly one lot, product, material or base.
    ONE_RECORD_REQUIRED = "ONE_RECORD_REQUIRED"
    #: No file the member may open has that id.
    FILE_NOT_FOUND = "FILE_NOT_FOUND"


class DocumentCreate(BaseModel):
    """A file to link, and the one lot, product, material or base it is
    linked to."""

    file_id: int
    lot_id: int | None = None
    product_id: int | None = None
    material_id: int | None = None
    base_id: int | None = None


def _one_record(**named: int | None) -> tuple[str, int]:
    """The one record ``named`` names, as its column and id, or 422
    ``ONE_RECORD_REQUIRED``."""
    given = [(name, record_id) for name, record_id in named.items() if record_id is not None]
    if len(given) != 1:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, DocumentError.ONE_RECORD_REQUIRED)
    return given[0]


class DocumentRead(BaseModel):
    """A linked file."""

    #: The link's id.
    id: int
    file_id: int
    #: The file's name; None when the member may not open it, or it is gone.
    name: str | None
    file_type: str | None
    created_at: datetime
    created_by: str | None


def _read(link: DocumentLink, file: FileFound | None) -> DocumentRead:
    return DocumentRead(
        id=link.id,
        file_id=link.file_id,
        name=file.name if file else None,
        file_type=file.file_type if file else None,
        created_at=link.created_at,
        created_by=link.created_by,
    )


async def _file(plugin: PluginDep, session: SessionClaims, file_id: int) -> FileFound | None:
    """The file the member may open by ``file_id``, without its body, or None."""
    try:
        answer = await read_for_member(
            plugin, session, f"/files/{file_id}", {"include_content": "false"}
        )
    except InitiativeRefused as refused:
        if refused.status_code >= 500:
            raise unavailable() from None
        return None
    return file_found(answer)


_LIST: Responses = {**MEMBER_READ, 422: {"description": "ONE_RECORD_REQUIRED"}}


@router.get("", responses=_LIST)
async def list_documents(
    db: OrganizationSessionDep,
    plugin: PluginDep,
    session: SessionDep,
    lot_id: int | None = None,
    product_id: int | None = None,
    material_id: int | None = None,
    base_id: int | None = None,
) -> list[DocumentRead]:
    """The files linked to one lot, product, material or base, in the order
    they were linked, each named as the member may see it."""
    name, record_id = _one_record(
        lot_id=lot_id, product_id=product_id, material_id=material_id, base_id=base_id
    )
    linked = await documents.links(db, **{name: record_id})
    files = await asyncio.gather(*(_file(plugin, session, link.file_id) for link in linked))
    return [_read(link, file) for link, file in zip(linked, files, strict=True)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        **MEMBER_READ,
        404: {"description": "NOT_FOUND, FILE_NOT_FOUND"},
        422: {"description": "ONE_RECORD_REQUIRED"},
    },
)
async def link_document(
    body: DocumentCreate, db: WriteSessionDep, plugin: PluginDep, session: SessionDep
) -> DocumentRead:
    """Link a file the member may open to one lot, product, material or base;
    linking it again answers the link."""
    name, record_id = _one_record(**body.model_dump(exclude={"file_id"}))
    await found(db, _RECORDS[name], record_id)
    file = await _file(plugin, session, body.file_id)
    if file is None:
        raise refuse(status.HTTP_404_NOT_FOUND, DocumentError.FILE_NOT_FOUND)
    link = await documents.link(db, body.file_id, **{name: record_id})
    await db.commit()
    return _read(link, file)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "NOT_FOUND"}},
)
async def unlink_document(document_id: int, db: WriteSessionDep) -> None:
    """Unlink a file; the file stays in Initiative."""
    await db.delete(await found(db, DocumentLink, document_id))
    await db.commit()


class DocumentBackupPeriod(BaseModel):
    """How long the organization's rules ask for records to be kept."""

    #: The longest any of the rules it keeps records for asks; None for none.
    months: int | None


@router.get("/backup-period")
async def read_backup_period(db: OrganizationSessionDep) -> DocumentBackupPeriod:
    """How long the rules the organization keeps records for ask them kept:
    the longest any of them asks."""
    settings = (await db.exec(select(OrganizationSettings))).first()
    ticked = settings.regimes if settings else []
    return DocumentBackupPeriod(months=backups.period(Regime(regime) for regime in ticked))
