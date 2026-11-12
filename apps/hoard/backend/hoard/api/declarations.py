"""Declarations: ingredient lists and allergens, rolled up through recipes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/declarations", tags=["declarations"])
