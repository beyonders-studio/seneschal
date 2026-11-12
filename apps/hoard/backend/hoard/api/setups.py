"""Starting setups: what one adds to an organization."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/setups", tags=["setups"])
