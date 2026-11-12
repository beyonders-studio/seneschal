"""Tracking production runs and purchases with Initiative tasks.

* ``link-task``, public, which a community's automations call once they have
  made the task for a record: stores the :class:`~hoard.records.TaskLink` and
  writes Hoard's fields on the task.
* ``complete-run`` and ``receive-purchase``, the writes a task's buttons run
  (``viewer`` and ``subject`` name the reader and the task).
* Sending the event outbox (``hoard.events``) to Initiative: :func:`deliver`
  after a commit that recorded events, and :func:`deliver_forever` in the
  app's lifespan, retrying what could not be sent. The fields on a record's
  task are written again after each event about it.
"""

from __future__ import annotations

from typing import Any

from seneschal_core.api import Plugin
from seneschal_core.platform import EndpointCall


async def link_task(call: EndpointCall) -> dict[str, Any]:
    """Answer ``link-task``: its ``record``, ``record_id`` and ``task_id``."""
    raise NotImplementedError


async def complete_run(call: EndpointCall) -> dict[str, Any]:
    """Answer ``complete-run`` for the run the subject task tracks."""
    raise NotImplementedError


async def receive_purchase(call: EndpointCall) -> dict[str, Any]:
    """Answer ``receive-purchase`` for the purchase the subject task tracks."""
    raise NotImplementedError


async def deliver(plugin: Plugin, organization_id: int) -> None:
    """Send what the organization's outbox holds that is due."""


async def deliver_forever(plugin: Plugin) -> None:
    """Send every organization's due events, again and again, until cancelled."""
