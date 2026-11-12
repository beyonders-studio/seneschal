"""A lot's trace, back and forward, and recalls (``hoard.trace``,
``hoard.recall``), with their exports."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/trace", tags=["trace"])
