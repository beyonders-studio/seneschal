"""Listings: each store product or variant and the item it sells, matched or
linked by hand (``hoard.listings``)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/listings", tags=["listings"])
