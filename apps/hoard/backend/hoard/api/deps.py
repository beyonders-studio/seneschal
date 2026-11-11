"""What Hoard's routes depend on beside the core's (``seneschal_core.api``):
what the page's member may see and do, and Hoard's own refusals.

* :data:`SeesCostsDep`: whether the member holds Hoard's ``costs`` permission
  where the page was opened. Without it, no cost or price is sent, and none is
  taken.
* :data:`AdminDep`: the session of a member who administers the community, or
  403 ``ADMIN_ONLY``.

Codes every area may refuse with are :class:`HoardError`. An area's own codes
are a ``StrEnum`` in its router's module, refused through
:func:`~seneschal_core.api.refuse` the same way; pages translate each one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import Depends, status

from hoard import plugin_manifest
from seneschal_core.api import SessionDep, refuse
from seneschal_core.sessions import SessionClaims


class HoardError(StrEnum):
    """Why Hoard refused a request, in any area."""

    #: Only a member who administers the community may do this.
    ADMIN_ONLY = "ADMIN_ONLY"
    #: No such record in the organization.
    NOT_FOUND = "NOT_FOUND"
    #: Initiative could not be asked, or did not answer.
    INITIATIVE_UNAVAILABLE = "INITIATIVE_UNAVAILABLE"


def _sees_costs(session: SessionDep) -> bool:
    return plugin_manifest.SEE_COSTS in session.permissions


SeesCostsDep = Annotated[bool, Depends(_sees_costs)]


def _admin(session: SessionDep) -> SessionClaims:
    if not session.admin:
        raise refuse(status.HTTP_403_FORBIDDEN, HoardError.ADMIN_ONLY)
    return session


AdminDep = Annotated[SessionClaims, Depends(_admin)]
