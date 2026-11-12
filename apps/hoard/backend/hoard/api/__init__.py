"""Hoard's API under ``/api/v1``: whether it is up, a page's sign-in, and one
router per area, each in its own module of this package."""

from __future__ import annotations

from fastapi import APIRouter

from hoard.api import (
    adjustments,
    bases,
    contacts,
    declarations,
    export,
    health,
    layouts,
    listings,
    materials,
    members,
    orders,
    products,
    properties,
    purchases,
    recipes,
    reports,
    runs,
    settings,
    setups,
    shipments,
    stocktakes,
    stores,
    tracking,
)
from seneschal_core.auth import router as auth_router

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth_router)
for area in (
    settings,
    materials,
    products,
    purchases,
    adjustments,
    stocktakes,
    contacts,
    reports,
    export,
    members,
    properties,
    layouts,
    bases,
    recipes,
    runs,
    tracking,
    declarations,
    setups,
    orders,
    shipments,
    stores,
    listings,
):
    router.include_router(area.router)
