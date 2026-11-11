"""Adjustments: every change to stock, and changes made by hand."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/adjustments", tags=["adjustments"])
