"""Hoard's manifest: each endpoint it declares is answered, its widgets and
dashboard draw only its own reads, and its fields, parts, actions and events
name only what it declares, as the plug-in contract asks."""

from __future__ import annotations

import re

from hoard import events, plugin_manifest
from hoard.endpoints import ENDPOINTS

#: The plug-in contract's identifiers and metadata keys.
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
METADATA_KEY = re.compile(r"^[a-z][a-z0-9_.]*$")


def test_every_endpoint_it_declares_is_answered():
    endpoints = plugin_manifest.definition()["endpoints"]
    called = [endpoint for endpoint in endpoints if endpoint["direction"] != "emit"]

    assert {endpoint["id"] for endpoint in called} == set(ENDPOINTS)
    assert {endpoint["id"]: endpoint["permission"] for endpoint in called} == {
        **{endpoint_id: "use" for endpoint_id in ENDPOINTS},
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


def test_its_endpoints_are_its_own_and_only_link_task_is_public():
    endpoints = plugin_manifest.definition()["endpoints"]
    prefix = f"plugin.{plugin_manifest.PUBLIC_ID}."

    for endpoint in endpoints:
        assert endpoint["id"].startswith(prefix), endpoint["id"]
        assert IDENTIFIER.match(endpoint["id"].removeprefix(prefix)), endpoint["id"]
        returns = {r["key"]: r for r in endpoint.get("returns", [])}
        assert len(returns) == len(endpoint.get("returns", [])) <= 24, endpoint["id"]
        if "identity" in endpoint:
            assert endpoint["direction"] in ("write", "emit"), endpoint["id"]
            for key in endpoint["identity"]["key"]:
                assert not returns[key].get("list"), endpoint["id"]
        if endpoint["direction"] == "emit":
            assert {"params", "actors", "permission", "public"}.isdisjoint(endpoint)
    assert plugin_manifest.PUBLIC_ENDPOINTS == {plugin_manifest.LINK_TASK}


def test_each_event_is_emitted_as_hoard_records_it():
    emitted = {
        e["id"]: e for e in plugin_manifest.definition()["endpoints"] if e["direction"] == "emit"
    }

    assert set(emitted) == {plugin_manifest.event_id(name) for name in events.EventName}
    for name in events.EventName:
        emission = emitted[plugin_manifest.event_id(name)]
        assert [r["key"] for r in emission["returns"]] == [r.key for r in events.RETURNS[name]]
        assert emission["identity"]["kind"] == events.IDENTITIES[name].kind


def test_its_fields_parts_and_actions_name_only_what_it_declares():
    definition = plugin_manifest.definition()
    writes = {e["id"]: e for e in definition["endpoints"] if e["direction"] == "write"}
    fields = {field["key"]: field for field in definition["fields"]}
    actions = {action["id"]: action for action in definition["actions"]}

    assert len(fields) == len(definition["fields"])
    for key, field in fields.items():
        assert METADATA_KEY.match(key) and key.startswith("hoard."), key
        assert ("options" in field) == (field["kind"] in ("select", "multi_select")), key
    for action in actions.values():
        endpoint = writes[action["endpoint"]]
        params = {param["key"] for param in endpoint.get("params", [])}
        required = {p["key"] for p in endpoint.get("params", []) if p.get("required")}
        assert required <= set(action.get("params", [])) <= params, action["id"]

    def nodes(node: dict) -> list[dict]:
        return [node, *(found for child in node.get("children", []) for found in nodes(child))]

    for part in definition["parts"]:
        assert IDENTIFIER.match(part["id"])
        for node in nodes(part["tree"]):
            props = node.get("props", {})
            if node["type"] in ("field", "value"):
                assert set(part["on"]) <= set(fields[props["field"]]["on"]), part["id"]
            if node["type"] == "button":
                assert set(part["on"]) <= set(actions[props["action"]]["on"]), part["id"]
    blocks = {"actions", "dashboards", "endpoints", "fields", "pages", "parts", "widgets"}
    assert set(definition["features"]) == blocks & set(definition)
