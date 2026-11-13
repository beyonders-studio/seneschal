"""Initiative's files, searched for the member viewing the page, so that only
files they may open are found.

Hoard calls Initiative acting for that member
(:meth:`~seneschal_core.initiative_api.InitiativeApi.call`'s ``acting_for``),
which Initiative allows once the member has consented. Until then a search is
refused ``FILES_CONSENT_REQUIRED``, and the page asks the member through
``POST /files/consent``; they answer in Initiative.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from hoard.api.adjustments import Responses
from hoard.api.deps import HoardError
from seneschal_core.api import PluginDep, SessionDep, refuse
from seneschal_core.initiative_api import (
    ConsentRequired,
    InitiativeRefused,
    InitiativeUnavailable,
)
from seneschal_core.sessions import SessionClaims

router = APIRouter(prefix="/files", tags=["files"])

#: The most files one search finds.
FILES_PER_SEARCH = 20
#: What Hoard asks a member to let it do for them, as Initiative shows it.
CONSENT_LABEL = "Find the files you link to stock, and show their names"


class FileError(StrEnum):
    """Why a search of Initiative's files was refused."""

    #: The member has not let Hoard read Initiative's files for them.
    FILES_CONSENT_REQUIRED = "FILES_CONSENT_REQUIRED"


#: How a route that reads files for the member may be refused.
MEMBER_READ: Responses = {
    403: {"description": "FILES_CONSENT_REQUIRED"},
    503: {"description": "INITIATIVE_UNAVAILABLE"},
}


class FileFound(BaseModel):
    """An Initiative file, as a picker lists it."""

    id: int
    name: str
    #: Initiative's type of file: a document, a spreadsheet, an upload…
    file_type: str | None
    #: The initiative it belongs to.
    initiative_name: str | None


def file_found(item: Any) -> FileFound | None:
    """One of Initiative's files read as :class:`FileFound`, or None when it
    is not one."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), int):
        return None
    initiative = item.get("initiative")
    file_type = item.get("file_type")
    return FileFound(
        id=item["id"],
        name=str(item.get("name") or ""),
        file_type=file_type if isinstance(file_type, str) else None,
        initiative_name=(
            str(initiative["name"])
            if isinstance(initiative, dict) and initiative.get("name")
            else None
        ),
    )


def unavailable() -> Exception:
    return refuse(status.HTTP_503_SERVICE_UNAVAILABLE, HoardError.INITIATIVE_UNAVAILABLE)


async def read_for_member(
    plugin: PluginDep, session: SessionClaims, path: str, params: dict[str, Any]
) -> Any:
    """Read ``path`` among the community's routes acting for the session's
    member. Refuses 403 ``FILES_CONSENT_REQUIRED`` until they consent, and 503
    when Initiative does not answer; Initiative's own refusals are raised."""
    try:
        return await plugin.api.call(
            session.community_ref, "GET", path, params=params, acting_for=session.member_ref
        )
    except ConsentRequired:
        raise refuse(status.HTTP_403_FORBIDDEN, FileError.FILES_CONSENT_REQUIRED) from None
    except InitiativeUnavailable:
        raise unavailable() from None


@router.get("", responses=MEMBER_READ)
async def search_files(
    plugin: PluginDep, session: SessionDep, search: str = Query(default="", max_length=200)
) -> list[FileFound]:
    """The files the member may open whose names match ``search``, most
    recently changed first; with nothing typed, the most recent."""
    params: dict[str, Any] = {"page_size": FILES_PER_SEARCH}
    if search.strip():
        params["search"] = search.strip()
    try:
        answer = await read_for_member(plugin, session, "/files/", params)
    except InitiativeRefused:
        raise unavailable() from None
    if not isinstance(answer, dict):
        raise unavailable()
    return [found for item in answer.get("items", []) if (found := file_found(item))]


class FileConsentRead(BaseModel):
    """Where the member's consent stands: ``pending`` until they answer in
    Initiative, then ``granted`` or ``denied``."""

    status: str


@router.post("/consent", responses={503: {"description": "INITIATIVE_UNAVAILABLE"}})
async def ask_files_consent(plugin: PluginDep, session: SessionDep) -> FileConsentRead:
    """Ask the member to let Hoard read Initiative's files for them; Initiative
    tells them, and they answer there. Asking again answers where it stands."""
    try:
        answer = await plugin.api.ask_consent(
            session.community_ref, session.member_ref, CONSENT_LABEL
        )
    except (InitiativeUnavailable, InitiativeRefused):
        raise unavailable() from None
    return FileConsentRead(status=str(answer.get("status") or "pending"))
