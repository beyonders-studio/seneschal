"""What Hoard tells Initiative it is: its plug-in manifest.

Hoard is a container plug-in. Its manifest names its service, the scopes it
asks a community to grant, and its pages: Stock, Production and Reports inside
each initiative it is placed in, and Settings for the whole community, which
only the community's admins open. Each page opens on Hoard's implicit ``use``
permission; seeing costs and prices takes :data:`SEE_COSTS` as well.

For dashboards it declares reads (``hoard.endpoints`` answers them), a widget
drawing each (the plain scripts in ``widgets/``, which Initiative runs in its
widget sandbox), and one dashboard arranging the first two, which publishing
Hoard lists on its own.

For the tasks that track its production runs and purchases it declares its
events (``hoard.events``), the fields it keeps on such a task, a part showing
them with a button, and two writes the buttons run: complete a run, and
receive a purchase in full. Another plug-in, such as a community's
automations, links a task to its record through the one public write.

``scripts/export_plugin.py`` writes it for the plug-in SDK, which checks it and
builds its registry source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hoard import events
from hoard.records import Allergen, RunStatus

#: ``<publisher>.<slug>``: the name a deployment registers Hoard under, and the
#: audience of every token Initiative signs for it.
PUBLIC_ID = "beyonders-studio.hoard"
#: The catalog id: minted once and never reused. Billing's statements name it.
UID = "JTH8EZG6V2964P"
NAME = "Hoard"
#: The plug-in protocol revision Hoard speaks.
PROTOCOL_VERSION = 1

#: What the install's tokens may act with. ``projects:read``: Hoard's values on
#: the tasks that track its records; ``contacts:read`` and ``contacts:write``:
#: suppliers and customers among the community's contacts; ``files:read``: the
#: documents its records link to; ``members:read``: names to show.
SCOPES = sorted(["contacts:read", "contacts:write", "files:read", "members:read", "projects:read"])

#: The permission to see what goods cost and what they sell for, held by an
#: initiative's roles. Members start without it.
SEE_COSTS = "costs"

#: What the community decides who may do with Hoard, beside the implicit ``use``.
PERMISSIONS = [
    {"id": SEE_COSTS, "name": {"en": "See costs and prices"}, "level": "initiative"},
]

#: Each page: its id, the path Hoard serves it at, where it renders, and its name.
PAGES = [
    ("stock", "/stock", "initiative", "Stock"),
    ("production", "/production", "initiative", "Production"),
    ("reports", "/reports", "initiative", "Reports"),
    ("settings", "/settings", "community", "Settings"),
]

#: What to reorder: the materials and products at or below their low-stock
#: limit. Every member who uses Hoard reads it.
REORDER = f"plugin.{PUBLIC_ID}.reorder"
#: What the stock on hand is worth, and in which currency. Only a member who
#: sees costs reads it.
VALUATION = f"plugin.{PUBLIC_ID}.valuation"
#: What was wasted, by reason, over the last days.
WASTE = f"plugin.{PUBLIC_ID}.waste"
#: Completed runs: what each planned to make against what it made.
YIELD = f"plugin.{PUBLIC_ID}.yield"
#: Which allergens each active product contains.
ALLERGEN_MATRIX = f"plugin.{PUBLIC_ID}.allergen-matrix"
#: Link a task to the production run or purchase it tracks. Public: a
#: community's automations call it.
LINK_TASK = f"plugin.{PUBLIC_ID}.link-task"
#: Complete the run the action's task tracks, at the actual quantity asked for.
COMPLETE_RUN = f"plugin.{PUBLIC_ID}.complete-run"
#: Receive every line of the purchase the action's task tracks.
RECEIVE_PURCHASE = f"plugin.{PUBLIC_ID}.receive-purchase"


def event_id(name: events.EventName) -> str:
    """The endpoint id an event is emitted as."""
    return f"plugin.{PUBLIC_ID}.{name.value}"


def _returns(*returns: tuple[str, str, str], list_: bool = False) -> list[dict[str, Any]]:
    return [
        {"key": key, "type": type_, "label": {"en": label}, **({"list": True} if list_ else {})}
        for key, type_, label in returns
    ]


#: How many days back the waste and planned-against-actual reports look.
DAYS_PARAM = {"key": "days", "type": "int", "label": {"en": "Days back"}}

ENDPOINTS: list[dict[str, Any]] = [
    {
        "id": REORDER,
        "direction": "read",
        "label": {"en": "Stock to reorder"},
        "description": {"en": "Materials and products at or below their low-stock limit"},
        "actors": ["installation"],
        "permission": "use",
        "returns": [
            {"key": key, "type": "string", "list": True, "label": {"en": label}}
            for key, label in (
                ("kind", "Material or product"),
                ("name", "Name"),
                ("unit", "Unit"),
                ("on_hand", "On hand"),
                ("low_stock_limit", "Low-stock limit"),
            )
        ],
    },
    {
        "id": VALUATION,
        "direction": "read",
        "label": {"en": "Stock value"},
        "description": {"en": "What the stock on hand is worth at its average cost"},
        "actors": ["installation"],
        "permission": SEE_COSTS,
        "returns": [
            {"key": "total", "type": "string", "label": {"en": "Total"}},
            {"key": "currency", "type": "string", "label": {"en": "Currency"}},
        ],
    },
    {
        "id": WASTE,
        "direction": "read",
        "label": {"en": "Waste by reason"},
        "description": {"en": "What was thrown away, by reason, over the last days"},
        "actors": ["installation"],
        "permission": "use",
        "params": [DAYS_PARAM],
        "returns": _returns(
            ("reason", "string", "Reason"),
            ("item", "string", "Item"),
            ("unit", "string", "Unit"),
            ("quantity", "string", "Quantity"),
            list_=True,
        ),
    },
    {
        "id": YIELD,
        "direction": "read",
        "label": {"en": "Planned against actual"},
        "description": {"en": "What each completed production run planned to make, and made"},
        "actors": ["installation"],
        "permission": "use",
        "params": [DAYS_PARAM],
        "returns": _returns(
            ("code", "string", "Run"),
            ("item", "string", "What it made"),
            ("completed_on", "string", "Completed on"),
            ("unit", "string", "Unit"),
            ("planned", "string", "Planned"),
            ("actual", "string", "Actual"),
            list_=True,
        ),
    },
    {
        "id": ALLERGEN_MATRIX,
        "direction": "read",
        "label": {"en": "Allergen matrix"},
        "description": {"en": "Which allergens each product contains"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("product", "string", "Product"),
            *(
                (allergen.value, "bool", allergen.value.replace("_", " ").capitalize())
                for allergen in Allergen
            ),
            list_=True,
        ),
    },
    {
        "id": LINK_TASK,
        "direction": "write",
        "label": {"en": "Link a task"},
        "description": {"en": "Track a production run or a purchase with a task"},
        "public": True,
        "actors": ["installation", "member"],
        "permission": "use",
        "params": [
            {
                "key": "record",
                "type": "select",
                "required": True,
                "label": {"en": "Production run or purchase"},
                "options": ["run", "purchase"],
            },
            {"key": "record_id", "type": "int", "required": True, "label": {"en": "Its id"}},
            {"key": "task_id", "type": "int", "required": True, "label": {"en": "Task"}},
        ],
        "returns": _returns(
            ("record", "string", "Production run or purchase"),
            ("record_id", "int", "Its id"),
            ("task_id", "int", "Task"),
        ),
    },
    {
        "id": COMPLETE_RUN,
        "direction": "write",
        "label": {"en": "Complete run"},
        "description": {"en": "Complete the production run a task tracks"},
        "actors": ["installation"],
        "permission": "use",
        "params": [
            {
                "key": "actual_quantity",
                "type": "string",
                "required": True,
                "label": {"en": "Actual quantity"},
            }
        ],
        "returns": _returns(
            ("run_id", "int", "Production run"),
            ("code", "string", "Code"),
            ("status", "string", "Status"),
        ),
        "identity": {"kind": "run", "key": ["run_id"]},
    },
    {
        "id": RECEIVE_PURCHASE,
        "direction": "write",
        "label": {"en": "Receive in full"},
        "description": {"en": "Receive every line of the purchase a task tracks"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("purchase_id", "int", "Purchase"),
            ("code", "string", "Code"),
            ("lines_received", "int", "Lines received"),
        ),
        "identity": {"kind": "purchase", "key": ["purchase_id"]},
    },
    *(
        {
            "id": event_id(name),
            "direction": "emit",
            "label": {"en": label},
            "returns": _returns(*((r.key, r.type, r.label) for r in events.RETURNS[name])),
            "identity": {
                "kind": events.IDENTITIES[name].kind,
                "key": list(events.IDENTITIES[name].key),
            },
        }
        for name, label in (
            (events.EventName.RUN_STARTED, "Production run started"),
            (events.EventName.RUN_COMPLETED, "Production run completed"),
            (events.EventName.PURCHASE_PLACED, "Purchase placed"),
            (events.EventName.PURCHASE_LINE_RECEIVED, "Purchase line received"),
            (events.EventName.PURCHASE_RECEIVED, "Purchase received in full"),
            (events.EventName.STOCK_LOW, "Stock at its low-stock limit"),
        )
    ),
]

#: The endpoints another plug-in may call.
PUBLIC_ENDPOINTS = frozenset(e["id"] for e in ENDPOINTS if e.get("public"))


def _field(key: str, kind: str, name: str, **more: Any) -> dict[str, Any]:
    return {"key": f"hoard.{key}", "kind": kind, "name": {"en": name}, "on": ["task"], **more}


def _options(*options: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"value": value, "label": {"en": label}} for value, label in options]


#: The values Hoard keeps on a task that tracks one of its records, which
#: ``hoard.tracking`` writes after each event about the record.
FIELDS: list[dict[str, Any]] = [
    _field("record", "text", "Hoard record"),
    _field(
        "status",
        "select",
        "Hoard status",
        options=_options(
            (RunStatus.NOT_STARTED.value, "Not started"),
            (RunStatus.IN_PROGRESS.value, "In progress"),
            (RunStatus.COMPLETED.value, "Completed"),
            ("ordered", "Ordered"),
            ("partly_received", "Partly received"),
            ("received", "Received"),
        ),
    ),
    _field("item", "text", "What it makes"),
    _field("quantity", "text", "Quantity"),
    _field(
        "materials",
        "select",
        "Materials",
        options=_options(("in_stock", "All in stock"), ("short", "Short")),
    ),
    _field("short", "number", "Materials short"),
    _field("assignee", "user_reference", "Made by"),
    _field("started", "date", "Start date"),
    _field("deadline", "date", "Deadline"),
    _field("ordered", "date", "Ordered on"),
    _field("received", "scale", "Lines received"),
]

#: The writes a reader runs from a task: an action each, by id.
COMPLETE_RUN_ACTION = "complete-run"
RECEIVE_PURCHASE_ACTION = "receive-purchase"

ACTIONS: list[dict[str, Any]] = [
    {
        "id": COMPLETE_RUN_ACTION,
        "name": {"en": "Complete run"},
        "endpoint": COMPLETE_RUN,
        "params": ["actual_quantity"],
        "on": ["task"],
        "menu": True,
    },
    {
        "id": RECEIVE_PURCHASE_ACTION,
        "name": {"en": "Received in full"},
        "endpoint": RECEIVE_PURCHASE,
        "on": ["task"],
        "confirm": {"en": "Receive every line of this purchase?"},
        "menu": True,
    },
]


def _section(title: str, fields: list[str], action: str) -> dict[str, Any]:
    return {
        "type": "section",
        "props": {"title": {"en": title}},
        "children": [
            *({"type": "field", "props": {"field": f"hoard.{field}"}} for field in fields),
            {"type": "button", "props": {"action": action, "variant": "secondary"}},
        ],
    }


#: Hoard's section on a task, for a run and for a purchase.
PARTS: list[dict[str, Any]] = [
    {
        "id": "run",
        "name": {"en": "Production run"},
        "description": {"en": "The run this task tracks, and a button to complete it"},
        "on": ["task"],
        "tree": _section(
            "Production run",
            [
                "record",
                "status",
                "item",
                "quantity",
                "materials",
                "short",
                "assignee",
                "started",
                "deadline",
            ],
            COMPLETE_RUN_ACTION,
        ),
    },
    {
        "id": "purchase",
        "name": {"en": "Purchase"},
        "description": {"en": "The purchase this task tracks, and a button to receive it"},
        "on": ["task"],
        "tree": _section(
            "Purchase", ["record", "status", "ordered", "received"], RECEIVE_PURCHASE_ACTION
        ),
    },
]

#: Where each widget's script is kept, named by its id.
WIDGETS_DIR = Path(__file__).parent / "widgets"

#: Each widget: its id, name, description, the read it draws, and what that
#: read answers in a preview.
WIDGETS: list[tuple[str, str, str, str, dict[str, Any]]] = [
    (
        "stock-to-reorder",
        "Stock to reorder",
        "Materials and products at or below their low-stock limit",
        REORDER,
        {
            "kind": ["material", "product"],
            "name": ["Flour", "Sourdough loaf"],
            "unit": ["kg", None],
            "on_hand": ["2.500000", "0"],
            "low_stock_limit": ["5.000000", "12.000000"],
        },
    ),
    (
        "stock-value",
        "Stock value",
        "What the stock on hand is worth at its average cost",
        VALUATION,
        {"total": "12840.500000", "currency": "USD"},
    ),
    (
        "waste-by-reason",
        "Waste by reason",
        "What was thrown away, by reason, over the last days",
        WASTE,
        {
            "reason": ["Expired", "Damaged"],
            "item": ["Oat milk", "Cups"],
            "unit": ["l", None],
            "quantity": ["4.000000", "25.000000"],
        },
    ),
    (
        "planned-against-actual",
        "Planned against actual",
        "What each completed production run planned to make, and made",
        YIELD,
        {
            "code": ["R-0012", "R-0013"],
            "item": ["Cold brew", "Chai spice base"],
            "completed_on": ["2026-11-03", "2026-11-05"],
            "unit": ["l", "g"],
            "planned": ["20.000000", "500.000000"],
            "actual": ["18.500000", "500.000000"],
        },
    ),
    (
        "allergen-matrix",
        "Allergen matrix",
        "Which allergens each product contains",
        ALLERGEN_MATRIX,
        {
            "product": ["Chai latte", "Oat chai latte"],
            **{allergen.value: [allergen is Allergen.MILK, False] for allergen in Allergen},
        },
    ),
]

#: The overview dashboard Hoard ships with, a catalog listing of its own: its
#: uid is minted once and never reused, like Hoard's.
OVERVIEW_UID = "EBFZPFVWHKT9H4"
OVERVIEW_PUBLIC_ID = "beyonders-studio.hoard-overview"

DASHBOARDS: list[dict[str, Any]] = [
    {
        "uid": OVERVIEW_UID,
        "public_id": OVERVIEW_PUBLIC_ID,
        "name": "Stock overview",
        "description": "What your stock is worth, and what to reorder.",
        "layout": {"columns": 12},
        "widgets": [
            {
                "type": "stock-value",
                "binding": {"endpoint_id": VALUATION},
                "grid": {"x": 0, "y": 0, "w": 4, "h": 3},
            },
            {
                "type": "stock-to-reorder",
                "binding": {"endpoint_id": REORDER},
                "grid": {"x": 4, "y": 0, "w": 8, "h": 6},
            },
        ],
    }
]


def definition() -> dict[str, Any]:
    """The manifest: what Hoard offers and asks for."""
    return {
        "plugin_kind": "service",
        "service": {"public_id": PUBLIC_ID, "protocol": PROTOCOL_VERSION, "scopes": SCOPES},
        "features": ["actions", "dashboards", "endpoints", "fields", "pages", "parts", "widgets"],
        "permissions": PERMISSIONS,
        "pages": [
            {"id": page, "path": path, "scopes": [scope], "name": {"en": name}}
            for page, path, scope, name in PAGES
        ],
        "endpoints": ENDPOINTS,
        "fields": FIELDS,
        "parts": PARTS,
        "actions": ACTIONS,
        "widgets": [
            {
                "id": widget,
                "meta": {"name": {"en": name}, "description": {"en": description}},
                "module_source": (WIDGETS_DIR / f"{widget}.js").read_text(encoding="utf-8"),
                "endpoints": [endpoint],
                "sample_data": {endpoint: sample},
            }
            for widget, name, description, endpoint, sample in WIDGETS
        ],
        "dashboards": DASHBOARDS,
    }


def built_plugin() -> dict[str, Any]:
    """Hoard as the plug-in SDK's ``initiative-plugin build --manifest`` reads it."""
    return {"publicId": PUBLIC_ID, "uid": UID, "name": NAME, "manifest": definition()}
