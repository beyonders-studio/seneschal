"""Production runs: making a variation or a base from its recipe, and completing it."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/runs", tags=["runs"])
