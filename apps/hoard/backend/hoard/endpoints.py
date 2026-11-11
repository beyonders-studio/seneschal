"""The endpoints Hoard declares to Initiative's plug-in platform, by endpoint
id: Initiative calls each at ``POST /v1/endpoints`` with a context token, for a
widget or another plug-in."""

from __future__ import annotations

from seneschal_core.platform import EndpointHandler

ENDPOINTS: dict[str, EndpointHandler] = {}
