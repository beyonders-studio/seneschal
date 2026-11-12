"""Shipments of an order's lines, the lots they draw, and what comes back
(``hoard.shipments``)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/shipments", tags=["shipments"])
