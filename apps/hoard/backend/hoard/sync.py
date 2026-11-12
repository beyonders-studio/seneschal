"""Store orders in: the nudges a store's webhooks leave, worked by a worker
the app's lifespan runs, each refetching its record from the store; orders
copied in, bundles split into their parts, claiming stock once paid; the
store's fulfillments recorded as shipments, their lots assumed; cancellations;
the import of the store's recent orders when it connects; and the
reconcile on Initiative's schedule, for whatever a webhook missed."""

from __future__ import annotations

from datetime import datetime

from hoard.channels import Connector
from hoard.records import Store
from seneschal_core.api import Plugin


async def work_forever(plugin: Plugin) -> None:
    """Work every organization's waiting nudges, again and again, until cancelled."""


async def reconcile(
    plugin: Plugin,
    organization_id: int,
    store: Store,
    connector: Connector,
    since: datetime | None,
) -> None:
    """Copy in the store's orders and products changed since ``since``, when
    the reconcile last succeeded, or since the store's own stamps."""
    raise NotImplementedError


async def import_history(
    plugin: Plugin, organization_id: int, store: Store, connector: Connector
) -> None:
    """Copy in the orders of the window the store keeps, moving no stock but
    for those still unfulfilled, and stamp the store imported."""
    raise NotImplementedError
