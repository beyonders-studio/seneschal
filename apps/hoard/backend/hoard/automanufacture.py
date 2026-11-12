"""Auto-manufacture: a store order that claims stock makes what it sells, by
a production run that completes at once, when the variation, its product or
the organization says so: always, or only for what is out of stock. Such a
run records no events."""

from __future__ import annotations

from hoard.records import Order
from seneschal_core.db import WriteSession


async def on_claimed(db: WriteSession, order: Order) -> None:
    """Make what ``order``, a store order that has just claimed its stock,
    sells, where auto-manufacture says so."""
    raise NotImplementedError
