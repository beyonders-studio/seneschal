"""Hoard's API under ``/api/v1``: whether it is up, a page's sign-in, the
jobs it runs in the background, search across its records, and one router per
area, each in its own module of this package."""

from __future__ import annotations

from fastapi import APIRouter

from hoard.api import (
    adjustments,
    bases,
    contacts,
    declarations,
    documents,
    export,
    files,
    health,
    holds,
    items,
    layouts,
    listings,
    lots,
    materials,
    members,
    orders,
    products,
    properties,
    purchases,
    recipes,
    reports,
    runs,
    scans,
    settings,
    setups,
    shipments,
    stocktakes,
    stores,
    trace,
    tracking,
)
from seneschal_core import jobs, search
from seneschal_core.auth import router as auth_router
from seneschal_core.sign_in import router as sign_in_router

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth_router)
router.include_router(sign_in_router)
router.include_router(jobs.router)
router.include_router(search.router)
for area in (
    settings,
    materials,
    products,
    items,
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
    lots,
    trace,
    holds,
    documents,
    files,
    scans,
):
    router.include_router(area.router)
