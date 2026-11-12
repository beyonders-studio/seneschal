"""How long the organization's backups are kept: the longest period any of
the rules it keeps records for sets. Keeping them is the infrastructure's;
Hoard shows the period in its settings."""

from __future__ import annotations

from collections.abc import Iterable

from hoard.records import Regime

#: The months each rule asks for records to be kept, the longest where it
#: sets a range: one up, one back asks 6 months to 2 years by shelf life, and
#: the EU's cosmetics information file is kept 10 years after a product's last
#: batch.
MONTHS: dict[Regime, int] = {
    Regime.US_ONE_UP_ONE_BACK: 24,
    Regime.US_FOOD_TRACEABILITY: 24,
    Regime.US_ORGANIC: 60,
    Regime.US_COSMETICS: 72,
    Regime.EU_FOOD: 60,
    Regime.EU_COSMETICS: 120,
    Regime.CANADA_FOOD: 24,
}


def period(regimes: Iterable[Regime]) -> int | None:
    """The months backups are kept for under ``regimes``: the longest any of
    them sets, or None for none."""
    return max((MONTHS[regime] for regime in regimes), default=None)
