"""Purchases of materials from suppliers, their lines, and receiving them."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/purchases", tags=["purchases"])
