"""Materials: what Hoard buys and never makes, and their stock per location."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/materials", tags=["materials"])
