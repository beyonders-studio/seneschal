"""Members' names, for the references Hoard's records keep.

A record names a member by the reference Initiative gives Hoard for them in the
community, never by name. Pages ask for the names of the references they show,
and Initiative is asked as the community's install.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from hoard.api.deps import HoardError
from seneschal_core.api import OrganizationDep, PluginDep, refuse
from seneschal_core.initiative_api import (
    PEOPLE_PER_LOOKUP,
    InitiativeRefused,
    InitiativeUnavailable,
)

router = APIRouter(prefix="/members", tags=["members"])


class MemberName(BaseModel):
    member_ref: str
    #: Their name in the community; null for a reference that no longer names
    #: a member, whom pages call a former member.
    name: str | None


@router.get(
    "",
    responses={
        401: {"description": "NOT_AUTHENTICATED"},
        503: {"description": "INITIATIVE_UNAVAILABLE"},
    },
)
async def list_member_names(
    organization: OrganizationDep,
    plugin: PluginDep,
    member_ref: Annotated[list[str], Query(min_length=1, max_length=PEOPLE_PER_LOOKUP)],
) -> list[MemberName]:
    """The names of the members ``member_ref`` names, in the order asked."""
    try:
        people = await plugin.api.people(organization.community_ref, member_ref)
    except (InitiativeUnavailable, InitiativeRefused):
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE
        ) from None
    return [
        MemberName(member_ref=ref, name=person.name if (person := people.get(ref)) else None)
        for ref in dict.fromkeys(member_ref)
    ]
