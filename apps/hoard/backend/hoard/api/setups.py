"""Starting setups (``hoard.setups``): which Hoard offers and which the
organization has added. Every member reads them; only a community admin adds
one, sending the words its keys take in the admin's language, or starts
blank, and only before the organization has begun."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard import setups
from hoard.api.deps import AdminDep, HoardError
from seneschal_core.api import OrganizationSessionDep, WriteSessionDep, refuse

router = APIRouter(prefix="/setups", tags=["setups"])

_READ: dict[int | str, dict[str, Any]] = {401: {"description": "NOT_AUTHENTICATED"}}
_BLANK: dict[int | str, dict[str, Any]] = {
    **_READ,
    403: {"description": "ADMIN_ONLY, READ_ONLY"},
}
_CHANGE: dict[int | str, dict[str, Any]] = {
    **_BLANK,
    404: {"description": "NOT_FOUND"},
    409: {"description": "SETUP_STARTED"},
    422: {"description": "WORDS_MISSING"},
}

Word = Annotated[str, Field(min_length=1, max_length=100)]


class SetupRead(BaseModel):
    key: str
    #: Whether the organization has added it.
    added: bool
    #: The keys whose words adding it sends, which the page's locale holds
    #: under the setup's key.
    words: list[str]


class SetupsRead(BaseModel):
    setups: list[SetupRead]
    #: Whether the organization has begun on its own (an admin chose to start
    #: blank, or it has a unit), so it is no longer asked for a setup.
    started: bool


class SetupAdd(BaseModel):
    #: The word for each of the setup's keys.
    words: dict[str, Word]


async def _read(db: AsyncSession, setup: setups.Setup) -> SetupRead:
    return SetupRead(key=setup.key, added=await setups.added(db, setup), words=setup.words())


async def _read_all(db: AsyncSession) -> SetupsRead:
    return SetupsRead(
        setups=[await _read(db, setup) for setup in setups.setups().values()],
        started=await setups.started(db),
    )


@router.get("", responses=_READ)
async def list_setups(db: OrganizationSessionDep) -> SetupsRead:
    """The setups Hoard offers, and whether the organization has added each."""
    return await _read_all(db)


@router.post("/blank", responses=_BLANK)
async def start_blank(_admin: AdminDep, db: WriteSessionDep) -> SetupsRead:
    """Start without a setup: the business is none Hoard has one for."""
    await setups.start_blank(db)
    await db.commit()
    return await _read_all(db)


@router.post("/{setup_key}/add", responses=_CHANGE)
async def add_setup(
    setup_key: str, body: SetupAdd, _admin: AdminDep, db: WriteSessionDep
) -> SetupRead:
    """Add what a setup brings, once: an organization that has begun, with a
    setup or blank, adds none."""
    setup = setups.setups().get(setup_key)
    if setup is None:
        raise refuse(status.HTTP_404_NOT_FOUND, HoardError.NOT_FOUND)
    if await setups.started(db):
        raise refuse(status.HTTP_409_CONFLICT, setups.SetupError.SETUP_STARTED)
    try:
        await setups.add(db, setup, body.words)
    except setups.SetupRefused as refused:
        raise refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, refused.code) from None
    await db.commit()
    return await _read(db, setup)
