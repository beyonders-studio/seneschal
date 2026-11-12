"""How long the organization's backups are kept: the longest period any of
the rules it keeps records for sets. Keeping them is the infrastructure's;
Hoard shows the period in its settings."""

from __future__ import annotations

from collections.abc import Iterable

from hoard.records import Regime


def period(regimes: Iterable[Regime]) -> int | None:
    """The months backups are kept for under ``regimes``: the longest any of
    them sets, or None for none."""
    raise NotImplementedError
