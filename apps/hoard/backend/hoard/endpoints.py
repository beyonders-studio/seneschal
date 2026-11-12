"""The endpoints Hoard declares to Initiative's plug-in platform, by endpoint
id: Initiative calls each at ``POST /v1/endpoints`` with a context token, for a
widget or another plug-in. Each answers with the returns its declaration in
``hoard.plugin_manifest`` names, from the organization of the community the
token names.

Initiative holds a reader to the permission an endpoint declares before it
calls: what stock is worth is read only by a member who sees costs."""

from __future__ import annotations

from typing import Any

from hoard import plugin_manifest, reports
from seneschal_core.platform import EndpointCall, EndpointHandler, organization_session


async def _reorder(call: EndpointCall) -> dict[str, Any]:
    async with organization_session(call) as db:
        shortfalls = await reports.reorder(db)
    return {
        "kind": [shortfall.kind.value for shortfall in shortfalls],
        "name": [shortfall.name for shortfall in shortfalls],
        "unit": [shortfall.unit for shortfall in shortfalls],
        "on_hand": [str(shortfall.on_hand) for shortfall in shortfalls],
        "low_stock_limit": [str(shortfall.low_stock_limit) for shortfall in shortfalls],
    }


async def _valuation(call: EndpointCall) -> dict[str, Any]:
    async with organization_session(call) as db:
        valuation = await reports.valuation(db)
    return {"total": str(valuation.total), "currency": valuation.currency}


ENDPOINTS: dict[str, EndpointHandler] = {
    plugin_manifest.REORDER: _reorder,
    plugin_manifest.VALUATION: _valuation,
}
