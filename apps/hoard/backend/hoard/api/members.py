"""Members' names, for the references Hoard's records keep.

A record names a member by the reference Initiative gives Hoard for them in the
community, never by name. Pages ask for the names of the references they show,
and search for a member to name by their handle. Initiative is asked as the
community's install.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

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


_ANSWERS: dict[int | str, dict[str, Any]] = {
    401: {"description": "NOT_AUTHENTICATED"},
    503: {"description": "INITIATIVE_UNAVAILABLE"},
}


@contextmanager
def _asking_initiative() -> Iterator[None]:
    """Refuses (503) a call Initiative did not answer."""
    try:
        yield
    except (InitiativeUnavailable, InitiativeRefused):
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE
        ) from None


@router.get("", responses=_ANSWERS)
async def list_member_names(
    organization: OrganizationDep,
    plugin: PluginDep,
    member_ref: Annotated[list[str], Query(min_length=1, max_length=PEOPLE_PER_LOOKUP)],
) -> list[MemberName]:
    """The names of the members ``member_ref`` names, in the order asked."""
    with _asking_initiative():
        people = await plugin.api.people(organization.community_ref, member_ref)
    return [
        MemberName(member_ref=ref, name=person.name if (person := people.get(ref)) else None)
        for ref in dict.fromkeys(member_ref)
    ]


@router.get("/search", responses=_ANSWERS)
async def search_member_names(
    organization: OrganizationDep,
    plugin: PluginDep,
    search: Annotated[str, Query(max_length=100)] = "",
) -> list[MemberName]:
    """The members whose handle matches ``search``, closest first, or the
    community's first members for no ``search``: whom a person-valued property
    may name."""
    with _asking_initiative():
        people = await plugin.api.search_people(organization.community_ref, search.strip())
    return [MemberName(member_ref=person.member_ref, name=person.name) for person in people]
