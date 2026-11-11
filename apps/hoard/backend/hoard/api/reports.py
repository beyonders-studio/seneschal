"""Reports on stock: what to reorder, and what stock is worth."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/reports", tags=["reports"])
