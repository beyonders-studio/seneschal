"""Initiative files linked to lots, products, materials and bases
(``hoard.documents``)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["documents"])
