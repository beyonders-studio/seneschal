"""Cost of goods: what was shipped cost, at each item's average on the day it
shipped, less what came back; with the mass balance and fair-trade volume
the reports draw from it."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession


async def cost_of_goods(db: AsyncSession, start: date, end: date) -> Any:
    """What the goods shipped from ``start`` to ``end`` cost."""
    raise NotImplementedError
