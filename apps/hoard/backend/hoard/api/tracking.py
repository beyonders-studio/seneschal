"""The tasks that track production runs and purchases."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/tracking", tags=["tracking"])
