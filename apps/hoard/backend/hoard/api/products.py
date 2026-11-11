"""Products, their variations and their images, and their stock per location."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/products", tags=["products"])
