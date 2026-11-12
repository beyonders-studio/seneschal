"""Holds on lots and their reasons (``hoard.holds``)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/holds", tags=["holds"])
