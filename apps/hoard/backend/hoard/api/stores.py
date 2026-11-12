"""The community's connected stores: each one's state, its locations mapped
to Hoard's, its push log, and its products, read and edited live."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/stores", tags=["stores"])
