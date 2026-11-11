"""Stocktakes: counts of stock at a location, whose variances become adjustments."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/stocktakes", tags=["stocktakes"])
