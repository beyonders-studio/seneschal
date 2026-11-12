"""Hoard's API under ``/api/v1``: whether it is up, a page's sign-in, and one
router per area, each in its own module of this package."""

from __future__ import annotations

from fastapi import APIRouter

from hoard.api import (
    adjustments,
    contacts,
    export,
    health,
    layouts,
    materials,
    members,
    products,
    properties,
    purchases,
    reports,
    settings,
    stocktakes,
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
):
    router.include_router(area.router)
