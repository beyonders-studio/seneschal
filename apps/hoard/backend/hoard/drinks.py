"""The reads the Drinks setup's widgets draw: waste by reason, what completed
production runs planned against what they made, and the allergen matrix
(``hoard.declarations``)."""

from __future__ import annotations

from typing import Any

from seneschal_core.platform import EndpointCall


async def waste(call: EndpointCall) -> dict[str, Any]:
    """Answer ``waste``: what was thrown away over the last ``days``, by reason."""
    raise NotImplementedError


async def planned_against_actual(call: EndpointCall) -> dict[str, Any]:
    """Answer ``yield``: each run completed over the last ``days``."""
    raise NotImplementedError


async def allergen_matrix(call: EndpointCall) -> dict[str, Any]:
    """Answer ``allergen-matrix``: each active product, and which allergens it contains."""
    raise NotImplementedError
