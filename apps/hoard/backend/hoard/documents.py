"""Documents: Initiative files linked by id to lots, products, materials and
bases, such as a supplier's certificate, a traceability plan or a report of a
problem. Hoard keeps the link, never the file; several reports on one lot are
listed together."""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import DocumentLink
from seneschal_core.db import WriteSession


async def link(db: WriteSession, file_id: int, **record: int) -> DocumentLink:
    """Link a file to one record, named as ``lot_id``, ``product_id``,
    ``material_id`` or ``base_id``; linking it again finds the link."""
    raise NotImplementedError


async def links(db: AsyncSession, **record: int) -> list[DocumentLink]:
    """The files linked to one record, named as :func:`link` names it."""
    raise NotImplementedError
