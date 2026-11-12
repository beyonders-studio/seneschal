"""Orders: by hand for a customer among the community's contacts, or copied
in from a store, with their lines and what each commits (``hoard.orders``)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])
