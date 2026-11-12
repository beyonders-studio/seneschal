"""The backup period the ticked rules ask for."""

from __future__ import annotations

from hoard import backups
from hoard.records import Regime


def test_every_rule_sets_a_period():
    assert set(backups.MONTHS) == set(Regime)


def test_the_longest_rule_sets_the_period():
    assert backups.period([]) is None
    assert backups.period([Regime.CANADA_FOOD]) == 24
    assert backups.period([Regime.US_ONE_UP_ONE_BACK, Regime.EU_FOOD]) == 60
    assert backups.period([Regime.US_COSMETICS, Regime.EU_COSMETICS, Regime.US_ORGANIC]) == 120
