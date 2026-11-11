"""Each kind of record, exported as CSV or JSON."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/export", tags=["export"])
