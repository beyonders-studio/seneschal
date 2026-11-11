"""The endpoints Hoard declares to Initiative's plug-in platform, by endpoint
id: Initiative calls each at ``POST /v1/endpoints`` with a context token, for a
widget or another plug-in. Each answers with the returns its declaration in
``hoard.plugin_manifest`` names, from the organization of the community the
token names.

Initiative holds a reader to the permission an endpoint declares before it
calls: what stock is worth is read only by a member who sees costs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import status

from hoard import plugin_manifest, reports
from seneschal_core import organizations
from seneschal_core.api import ErrorCode, refuse
from seneschal_core.db import OrganizationSession
from seneschal_core.platform import EndpointCall, EndpointHandler


@asynccontextmanager
async def _organization(call: EndpointCall) -> AsyncIterator[OrganizationSession]:
    """A session reading the organization of the call's community."""
    if call.claims.community_status in organizations.UNAVAILABLE:
        raise refuse(status.HTTP_403_FORBIDDEN, ErrorCode.COMMUNITY_UNAVAILABLE)
    async with call.plugin.database.session() as db:
        organization_id = await organizations.organization_for(
            db,
            community_ref=call.claims.community_ref,
            plugin_install_id=call.claims.plugin_install_id,
        )
        await db.commit()
    async with call.plugin.database.organization_session(organization_id) as db:
        yield db


async def _reorder(call: EndpointCall) -> dict[str, Any]:
    async with _organization(call) as db:
        shortfalls = await reports.reorder(db)
    return {
        "kind": [shortfall.kind.value for shortfall in shortfalls],
        "name": [shortfall.name for shortfall in shortfalls],
        "unit": [shortfall.unit for shortfall in shortfalls],
        "on_hand": [str(shortfall.on_hand) for shortfall in shortfalls],
        "low_stock_limit": [str(shortfall.low_stock_limit) for shortfall in shortfalls],
    }


async def _valuation(call: EndpointCall) -> dict[str, Any]:
    async with _organization(call) as db:
        valuation = await reports.valuation(db)
    return {"total": str(valuation.total), "currency": valuation.currency}


ENDPOINTS: dict[str, EndpointHandler] = {
    plugin_manifest.REORDER: _reorder,
    plugin_manifest.VALUATION: _valuation,
}
