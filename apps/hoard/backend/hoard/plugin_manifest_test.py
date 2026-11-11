"""Hoard's manifest: each read it declares is answered, and its widgets and
dashboard draw only those reads."""

from __future__ import annotations

from hoard import plugin_manifest
from hoard.endpoints import ENDPOINTS


def test_every_endpoint_it_declares_is_answered():
    endpoints = plugin_manifest.definition()["endpoints"]

    assert {endpoint["id"] for endpoint in endpoints} == set(ENDPOINTS)
    assert {endpoint["id"]: endpoint["permission"] for endpoint in endpoints} == {
        plugin_manifest.REORDER: "use",
        plugin_manifest.VALUATION: plugin_manifest.SEE_COSTS,
    }


def test_its_widgets_and_dashboard_draw_its_own_reads():
    definition = plugin_manifest.definition()
    reads = {e["id"] for e in definition["endpoints"] if e["direction"] == "read"}
    widgets = {widget["id"]: widget for widget in definition["widgets"]}

    for widget in widgets.values():
        assert "function render(" in widget["module_source"], widget["id"]
        assert set(widget["endpoints"]) <= reads, widget["id"]
        assert set(widget["sample_data"]) == set(widget["endpoints"]), widget["id"]
    for dashboard in definition["dashboards"]:
        for tile in dashboard["widgets"]:
            assert tile["binding"]["endpoint_id"] in widgets[tile["type"]]["endpoints"]
