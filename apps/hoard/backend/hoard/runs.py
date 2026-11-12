"""Production runs: their lines, filled from a recipe, and completing them.

Only completing a run moves stock (``hoard.costing`` costs it), and a
completed run can be reopened.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hoard.records import ProductionRun
from seneschal_core.db import WriteSession


async def complete(
    db: WriteSession, run: ProductionRun, actual_quantity: Decimal, completed_on: date
) -> None:
    """Complete ``run``, having made ``actual_quantity`` on ``completed_on``."""
    raise NotImplementedError
