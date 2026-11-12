"""Bases: made from materials and other bases, never bought or sold."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/bases", tags=["bases"])
