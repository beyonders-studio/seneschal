"""Documents: Initiative files linked by id to lots, products, materials and
bases, such as a supplier's certificate, a traceability plan or a report of a
problem. Hoard keeps the link, never the file; several reports on one lot are
listed together."""

from __future__ import annotations

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import DocumentLink
from seneschal_core.db import WriteSession

#: How a record a file is linked to is named.
RECORDS = ("lot_id", "product_id", "material_id", "base_id")


def _named(record: dict[str, int]) -> tuple[str, int]:
    """The one record ``record`` names, as its column and id."""
    if len(record) != 1 or not set(record) <= set(RECORDS):
        raise ValueError(f"name one of {', '.join(RECORDS)}, not {sorted(record)}")
    [(name, record_id)] = record.items()
    return name, record_id


async def link(db: WriteSession, file_id: int, **record: int) -> DocumentLink:
    """Link a file to one record, named as ``lot_id``, ``product_id``,
    ``material_id`` or ``base_id``; linking it again finds the link."""
    name, record_id = _named(record)
    existing = (
        await db.exec(
            select(DocumentLink).where(
                col(getattr(DocumentLink, name)) == record_id, DocumentLink.file_id == file_id
            )
        )
    ).first()
    if existing is not None:
        return existing
    made = DocumentLink(organization_id=db.organization_id, file_id=file_id)
    setattr(made, name, record_id)
    db.add(made)
    await db.flush()
    return made


async def links(db: AsyncSession, **record: int) -> list[DocumentLink]:
    """The files linked to one record, named as :func:`link` names it, in the
    order they were linked."""
    name, record_id = _named(record)
    return list(
        await db.exec(
            select(DocumentLink)
            .where(col(getattr(DocumentLink, name)) == record_id)
            .order_by(col(DocumentLink.created_at), col(DocumentLink.id))
        )
    )
