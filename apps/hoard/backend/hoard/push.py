"""Pushing what is available to each store: a push queued for each listing
whose item's stock changed, once the change commits, sent by a worker the
app's lifespan runs, retried with the store's compare-and-set where it has
one, and reconciled on Initiative's schedule. Each push is kept as the push
log (:class:`hoard.records.StockPush`)."""

from __future__ import annotations

from hoard.channels import Connector
from hoard.records import Store
from seneschal_core.api import Plugin


async def push_forever(plugin: Plugin) -> None:
    """Send every organization's queued pushes, again and again, until cancelled."""


async def reconcile(
    plugin: Plugin, organization_id: int, store: Store, connector: Connector
) -> None:
    """Push each of the store's listings what is available, wherever the store
    holds something else."""
    raise NotImplementedError
