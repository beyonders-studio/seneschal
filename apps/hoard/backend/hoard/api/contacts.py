"""Suppliers among the community's contacts in Initiative, read and added through
:attr:`~seneschal_core.api.Plugin.api`.

Hoard keeps no contacts: a purchase names its supplier by the contact's id.
Pages read the community's own contacts, those that belong to no initiative,
and add one there (``contacts:write``). Initiative keeps those in its
Community contacts plug-in, so adding one needs it installed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, StringConstraints

from hoard.api.adjustments import Responses
from hoard.api.deps import HoardError
from seneschal_core.api import OrganizationDep, PluginDep, WriteSessionDep, refuse
from seneschal_core.initiative_api import InitiativeRefused, InitiativeUnavailable

router = APIRouter(prefix="/contacts", tags=["contacts"])

#: The most contacts one page of Initiative's list holds.
CONTACTS_PER_PAGE = 500
UNAVAILABLE: Responses = {503: {"description": "INITIATIVE_UNAVAILABLE"}}
#: Initiative's refusal when the community has no Community contacts plug-in.
COMMUNITY_PLUGIN_REQUIRED = "CONTACT_COMMUNITY_PLUGIN_REQUIRED"


class ContactError(StrEnum):
    """Why adding a contact was refused."""

    #: The community has no Community contacts plug-in to keep it in.
    COMMUNITY_CONTACTS_NOT_INSTALLED = "COMMUNITY_CONTACTS_NOT_INSTALLED"
    #: Initiative did not let the community's install add it.
    CONTACT_ADD_REFUSED = "CONTACT_ADD_REFUSED"


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
    """Every one of the community's own contacts, by name."""
    found: list[ContactRead] = []
    page = 1
    while True:
        try:
            answer = await plugin.api.call(
                organization.community_ref,
                "GET",
                "/contacts/",
                params={"scope": "community", "page": page, "page_size": CONTACTS_PER_PAGE},
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
    responses={
        **UNAVAILABLE,
        403: {"description": "COMMUNITY_CONTACTS_NOT_INSTALLED, CONTACT_ADD_REFUSED"},
    },
)
async def create_contact(
    _db: WriteSessionDep,
    organization: OrganizationDep,
    plugin: PluginDep,
    body: ContactCreate,
) -> ContactRead:
    """Add one of the community's contacts, a supplier by default."""
    try:
        answer = await plugin.api.call(
            organization.community_ref,
            "POST",
            "/contacts/",
            body={"name": body.name, "is_organization": body.is_organization},
        )
    except InitiativeUnavailable:
        raise _unavailable() from None
    except InitiativeRefused as refused:
        reason = (
            ContactError.COMMUNITY_CONTACTS_NOT_INSTALLED
            if refused.detail == COMMUNITY_PLUGIN_REQUIRED
            else ContactError.CONTACT_ADD_REFUSED
        )
        raise refuse(status.HTTP_403_FORBIDDEN, reason) from None
    contact = _contact(answer)
    if contact is None:
        raise _unavailable()
    return contact
