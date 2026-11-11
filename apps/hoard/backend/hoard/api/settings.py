"""The organization's settings: its currency, units, categories, locations and
adjustment reasons. Every member reads them; only a community admin changes
them (:data:`~hoard.api.deps.AdminDep`)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from hoard.records import DEFAULT_CURRENCY, OrganizationSettings
from seneschal_core.api import OrganizationSessionDep

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsRead(BaseModel):
    #: The ISO 4217 code every cost and price is in.
    currency: str


@router.get("", responses={401: {"description": "NOT_AUTHENTICATED"}})
async def read_settings(db: OrganizationSessionDep) -> SettingsRead:
    """The organization's settings, each its default until it is changed."""
    settings = (await db.exec(select(OrganizationSettings))).first()
    return SettingsRead(currency=settings.currency if settings else DEFAULT_CURRENCY)
