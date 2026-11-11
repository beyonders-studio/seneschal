"""Suppliers among the community's contacts in Initiative, read and added through
:attr:`~seneschal_core.api.Plugin.api`.

Hoard keeps no contacts: a purchase names its supplier by the contact's id.
Pages read the contacts the community's install may see, and add one, which
Initiative keeps in the initiative the page was opened from (``contacts:write``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, StringConstraints

from hoard.api.adjustments import Responses
from hoard.api.deps import HoardError
from seneschal_core.api import (
    OrganizationDep,
    PluginDep,
    SessionDep,
    WriteSessionDep,
    refuse,
)
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable

router = APIRouter(prefix="/contacts", tags=["contacts"])

#: The most contacts one page of Initiative's list holds.
CONTACTS_PER_PAGE = 500
UNAVAILABLE: Responses = {503: {"description": "INITIATIVE_UNAVAILABLE"}}


class ContactError(StrEnum):
    """Why adding a contact was refused."""

    #: Initiative did not let the community's install add it there.
    CONTACT_REFUSED = "CONTACT_REFUSED"


class ContactRead(BaseModel):
    """A contact, as a supplier picker shows it."""

    id: int
    name: str
    #: An organization, rather than a person.
    is_organization: bool


def _contact(item: Any) -> ContactRead | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
        return None
    return ContactRead(
        id=item["id"],
        name=str(item.get("name") or ""),
        is_organization=bool(item.get("is_organization")),
    )


def _unavailable() -> Exception:
    return refuse(status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE)


@router.get("", responses=UNAVAILABLE)
async def list_contacts(organization: OrganizationDep, plugin: PluginDep) -> list[ContactRead]:
    """Every contact the community's install may see, by name."""
    found: list[ContactRead] = []
    page = 1
    while True:
        try:
            answer = await plugin.api.call(
                organization.community_ref,
                "GET",
                "/contacts/",
                params={"page": page, "page_size": CONTACTS_PER_PAGE},
            )
        except (InitiativeUnavailable, InitiativeRefused):
            raise _unavailable() from None
        if not isinstance(answer, dict):
            raise _unavailable()
        found.extend(c for item in answer.get("items", []) if (c := _contact(item)))
        if not answer.get("has_next"):
            return found
        page += 1


class ContactCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    is_organization: bool = True


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={**UNAVAILABLE, 403: {"description": "CONTACT_REFUSED"}},
)
async def create_contact(
    _db: WriteSessionDep,
    session: SessionDep,
    organization: OrganizationDep,
    plugin: PluginDep,
    body: ContactCreate,
) -> ContactRead:
    """Add a contact, a supplier by default, in the initiative the page was
    opened from."""
    if session.initiative_id is None:
        raise refuse(status.HTTP_403_FORBIDDEN, ContactError.CONTACT_REFUSED)
    try:
        answer = await plugin.api.call(
            organization.community_ref,
            "POST",
            "/contacts/",
            body={
                "name": body.name,
                "is_organization": body.is_organization,
                "initiative_id": session.initiative_id,
            },
        )
    except InitiativeUnavailable:
        raise _unavailable() from None
    except InitiativeRefused:
        raise refuse(status.HTTP_403_FORBIDDEN, ContactError.CONTACT_REFUSED) from None
    contact = _contact(answer)
    if contact is None:
        raise _unavailable()
    return contact
