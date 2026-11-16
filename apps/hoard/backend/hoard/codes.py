"""The codes Hoard gives what it numbers: ``R-0001`` for a production run,
``O-0001`` for an order, ``P-0001`` for a purchase.

Each organization numbers its own from 1. The next code follows the highest
number among the organization's codes of that kind, so a code typed by hand
in the same shape moves the count on, and one in any other shape is left out.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Numeric, cast, func
from sqlmodel import select

from seneschal_core.db import WriteSession


async def next_code(db: WriteSession, model: Any, prefix: str) -> str:
    """The next free code of ``model``'s kind in the organization, such as
    ``R-0012`` for ``prefix`` ``R``."""
    number = func.substring(model.code, f"^{prefix}-([0-9]+)$")
    latest = await db.scalar(select(func.max(cast(number, Numeric))))
    return f"{prefix}-{int(latest or 0) + 1:04d}"
