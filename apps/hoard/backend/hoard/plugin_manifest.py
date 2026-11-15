"""What Hoard tells Initiative it is: its plug-in manifest.

Hoard is a container plug-in. Its manifest names its service, the scopes it
asks a community to grant, and its pages: Stock, Production and Reports inside
each initiative it is placed in, and Settings for the whole community, which
only the community's admins open. Each page opens on Hoard's implicit ``use``
permission; seeing costs and prices takes :data:`SEE_COSTS` as well.

For dashboards it declares reads (``hoard.endpoints`` answers them), among
them the lots on hold ready to check and the production planning reads
(``hoard.planning``), a widget drawing each (the plain scripts in
``widgets/``, which Initiative runs in its widget sandbox), and two
dashboards, the stock overview and production planning, each of which
publishing Hoard lists on its own.

For sales channels it declares a connection per store it connects to, which
Hoard checks at the store when an admin saves it (``hoard.channels.hooks``),
the store's webhooks, which Initiative checks under the connection's signing
secret and routes by the store's address, and two schedules: ``reconcile``,
for what a webhook missed, and ``upkeep``.

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
from hoard.records import Allergen, Channel, RunStatus

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
    ("orders", "/orders", "initiative", "Orders"),
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
#: The lots on hold whose ready date has come, to check.
HOLDS_READY = f"plugin.{PUBLIC_ID}.holds-ready"
#: What to make next: each product and base to make, in whole batches.
MAKE_NEXT = f"plugin.{PUBLIC_ID}.make-next"
#: The materials the runs ahead take more of than there is, and when to order them.
MATERIALS_TO_ORDER = f"plugin.{PUBLIC_ID}.materials-to-order"
#: The hours of work in each of the next weeks.
HOURS_AHEAD = f"plugin.{PUBLIC_ID}.hours-ahead"
#: What one unit cost to make over the last two runs, against the store's
#: price. Only a member who sees costs reads it.
COST_TO_MAKE = f"plugin.{PUBLIC_ID}.cost-to-make"
#: The lots expiring soon, with what is left of each.
EXPIRING_SOON = f"plugin.{PUBLIC_ID}.expiring-soon"
#: What each product sold in each of the last weeks.
SALES_BY_WEEK = f"plugin.{PUBLIC_ID}.sales-by-week"
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
        "id": HOLDS_READY,
        "direction": "read",
        "label": {"en": "Holds ready to check"},
        "description": {"en": "Lots on hold whose ready date has come"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("lot", "string", "Lot"),
            ("item", "string", "Item"),
            ("reason", "string", "Reason"),
            ("ready_on", "string", "Ready on"),
            list_=True,
        ),
    },
    {
        "id": MAKE_NEXT,
        "direction": "read",
        "label": {"en": "What to make next"},
        "description": {
            "en": "Products and bases to make, in whole batches, and the hours they take"
        },
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("kind", "string", "Product or base"),
            ("item", "string", "What to make"),
            ("unit", "string", "Unit"),
            ("needed", "string", "Needed"),
            ("available", "string", "Available"),
            ("curing", "string", "Curing"),
            ("planned", "string", "In planned runs"),
            ("short", "string", "Short"),
            ("batches", "int", "Batches"),
            ("hours", "string", "Hours"),
            list_=True,
        ),
    },
    {
        "id": MATERIALS_TO_ORDER,
        "direction": "read",
        "label": {"en": "Materials to order"},
        "description": {
            "en": "Materials the runs ahead need more of, and the day to order them by"
        },
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("material", "string", "Material"),
            ("unit", "string", "Unit"),
            ("supplier", "string", "Supplier"),
            ("short", "string", "Short"),
            ("needed_on", "string", "Needed on"),
            ("order_by", "string", "Order by"),
            list_=True,
        ),
    },
    {
        "id": HOURS_AHEAD,
        "direction": "read",
        "label": {"en": "Production hours ahead"},
        "description": {"en": "Hours of production in each of the next four weeks"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("week", "string", "Week of"),
            ("planned", "string", "Planned and started runs"),
            ("suggested", "string", "Suggested runs"),
            list_=True,
        ),
    },
    {
        "id": COST_TO_MAKE,
        "direction": "read",
        "label": {"en": "Cost to make"},
        "description": {
            "en": "What one unit cost over the last two runs, against the store's price"
        },
        "actors": ["installation"],
        "permission": SEE_COSTS,
        "returns": [
            *_returns(
                ("product", "string", "Product"),
                ("last", "string", "Last run"),
                ("previous", "string", "Run before"),
                ("change", "string", "Change"),
                ("price", "string", "Store price"),
                ("margin", "string", "Margin"),
                list_=True,
            ),
            *_returns(("currency", "string", "Currency")),
        ],
    },
    {
        "id": EXPIRING_SOON,
        "direction": "read",
        "label": {"en": "Expiring soon"},
        "description": {"en": "Lots that expire within 30 days, with what is left of each"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("lot", "string", "Lot"),
            ("item", "string", "Item"),
            ("unit", "string", "Unit"),
            ("expires_on", "string", "Expires on"),
            ("days_left", "int", "Days left"),
            ("left", "string", "Left"),
            ("on_hold", "bool", "On hold"),
            list_=True,
        ),
    },
    {
        "id": SALES_BY_WEEK,
        "direction": "read",
        "label": {"en": "Sales by week"},
        "description": {"en": "Units of each product sold each week, over the last 12 weeks"},
        "actors": ["installation"],
        "permission": "use",
        "returns": _returns(
            ("product", "string", "Product"),
            ("week", "string", "Week of"),
            ("units", "string", "Units sold"),
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
            (events.EventName.HOLD_PLACED, "Lot put on hold"),
            (events.EventName.HOLD_READY, "Lot on hold ready to check"),
            (events.EventName.HOLD_ENDED, "Lot off hold"),
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
    (
        "holds-ready",
        "Holds ready to check",
        "Lots on hold whose ready date has come",
        HOLDS_READY,
        {
            "lot": ["R-0031"],
            "item": ["Lavender soap"],
            "reason": ["Curing"],
            "ready_on": ["2026-12-10"],
        },
    ),
    (
        "make-next",
        "What to make next",
        "Products and bases to make, in whole batches, and the hours they take",
        MAKE_NEXT,
        {
            "kind": ["product", "base"],
            "item": ["Lavender soap", "Soap base"],
            "unit": [None, "g"],
            "needed": ["64.000000", "4800.000000"],
            "available": ["12.000000", "1500.000000"],
            "curing": ["20.000000", "0"],
            "planned": ["0", "0"],
            "short": ["32.000000", "3300.000000"],
            "batches": [2, 2],
            "hours": ["3.00", "1.50"],
        },
    ),
    (
        "materials-to-order",
        "Materials to order",
        "Materials the runs ahead need more of, and the day to order them by",
        MATERIALS_TO_ORDER,
        {
            "material": ["Olive oil", "Lavender oil"],
            "unit": ["g", "ml"],
            "supplier": ["Mill & Co", None],
            "short": ["2400.000000", "35.000000"],
            "needed_on": ["2026-11-20", "2026-11-15"],
            "order_by": ["2026-11-13", None],
        },
    ),
    (
        "hours-ahead",
        "Production hours ahead",
        "Hours of production in each of the next four weeks",
        HOURS_AHEAD,
        {
            "week": ["2026-11-15", "2026-11-22", "2026-11-29", "2026-12-06"],
            "planned": ["6.00", "4.50", "0.00", "2.00"],
            "suggested": ["4.50", "0.00", "0.00", "0.00"],
        },
    ),
    (
        "cost-to-make",
        "Cost to make",
        "What one unit cost over the last two runs, against the store's price",
        COST_TO_MAKE,
        {
            "product": ["Cold brew", "Lavender soap"],
            "last": ["1.850000", "2.400000"],
            "previous": ["1.700000", None],
            "change": ["0.150000", None],
            "price": ["4.500000", "8.000000"],
            "margin": ["0.588889", "0.700000"],
            "currency": "USD",
        },
    ),
    (
        "expiring-soon",
        "Expiring soon",
        "Lots that expire within 30 days, with what is left of each",
        EXPIRING_SOON,
        {
            "lot": ["P-0042-3", "R-0031"],
            "item": ["Oat milk", "Cold brew"],
            "unit": ["l", None],
            "expires_on": ["2026-11-18", "2026-12-02"],
            "days_left": [3, 17],
            "left": ["6.000000", "24.000000"],
            "on_hold": [False, True],
        },
    ),
    (
        "sales-by-week",
        "Sales by week",
        "Units of each product sold each week, over the last 12 weeks",
        SALES_BY_WEEK,
        {
            "product": ["Cold brew"] * 3 + ["Lavender soap"] * 3,
            "week": ["2026-10-26", "2026-11-02", "2026-11-09"] * 2,
            "units": ["14.000000", "18.000000", "11.000000", "6.000000", "9.000000", "4.000000"],
        },
    ),
]

#: The overview dashboard Hoard ships with, a catalog listing of its own: its
#: uid is minted once and never reused, like Hoard's.
OVERVIEW_UID = "EBFZPFVWHKT9H4"
OVERVIEW_PUBLIC_ID = "beyonders-studio.hoard-overview"
#: The production planning dashboard, a catalog listing of its own like the
#: overview: its uid is minted once and never reused.
PLANNING_UID = "1K0FQNF693SZMW"
PLANNING_PUBLIC_ID = "beyonders-studio.hoard-planning"


def _tile(widget: str, endpoint: str, x: int, y: int, w: int, h: int) -> dict[str, Any]:
    return {
        "type": widget,
        "binding": {"endpoint_id": endpoint},
        "grid": {"x": x, "y": y, "w": w, "h": h},
    }


DASHBOARDS: list[dict[str, Any]] = [
    {
        "uid": OVERVIEW_UID,
        "public_id": OVERVIEW_PUBLIC_ID,
        "name": "Stock overview",
        "description": "What your stock is worth, and what to reorder.",
        "layout": {"columns": 12},
        "widgets": [
            _tile("stock-value", VALUATION, 0, 0, 4, 3),
            _tile("stock-to-reorder", REORDER, 4, 0, 8, 6),
        ],
    },
    {
        "uid": PLANNING_UID,
        "public_id": PLANNING_PUBLIC_ID,
        "name": "Production planning",
        "description": (
            "What to make next, the materials and hours it takes, what making costs, "
            "and what is about to expire."
        ),
        "layout": {"columns": 12},
        "widgets": [
            _tile("make-next", MAKE_NEXT, 0, 0, 8, 6),
            _tile("hours-ahead", HOURS_AHEAD, 8, 0, 4, 6),
            _tile("materials-to-order", MATERIALS_TO_ORDER, 0, 6, 8, 6),
            _tile("expiring-soon", EXPIRING_SOON, 8, 6, 4, 6),
            _tile("cost-to-make", COST_TO_MAKE, 0, 12, 8, 6),
            _tile("planned-against-actual", YIELD, 8, 12, 4, 6),
        ],
    },
]


#: The Shopify connection: what an admin types (the store's ``myshopify.com``
#: address and its app's client ID and secret), and what Hoard's
#: ``after_connect`` answers once the store accepts them: the store's own
#: address, which routes its webhooks, and the secret they are signed with.
SHOPIFY = Channel.SHOPIFY.value
CONNECTIONS: list[dict[str, Any]] = [
    {
        "id": SHOPIFY,
        "scope": "static",
        "label": {"en": "Shopify"},
        "after_connect": True,
        "fields": [
            {
                "key": key,
                "type": type_,
                "label": {"en": label},
                **({"managed": True} if managed else {"required": True}),
            }
            for key, type_, label, managed in (
                ("shop", "string", "Store address (myshopify.com)", False),
                ("client_id", "string", "Client ID", False),
                ("client_secret", "secret", "Client secret", False),
                ("shop_domain", "string", "Store domain", True),
                ("signing_secret", "secret", "Webhook signing secret", True),
            )
        ],
    }
]

#: Shopify's webhooks: each signed with the store's app's secret, named by its
#: webhook id, and routed to the community whose connection holds the store.
WEBHOOKS: dict[str, Any] = {
    "verify": {
        "scheme": "hmac_sha256",
        "header": "X-Shopify-Hmac-Sha256",
        "encoding": "base64",
        "secret": "{signing_secret}",
    },
    "dedup": "X-Shopify-Webhook-Id",
    "route": {"header": "X-Shopify-Shop-Domain", "connection": SHOPIFY, "field": "shop_domain"},
}

#: What Initiative runs for each community: each store reconciled, and its
#: subscriptions kept.
SCHEDULES = [{"id": "reconcile", "every": "15m"}, {"id": "upkeep", "every": "24h"}]


def definition() -> dict[str, Any]:
    """The manifest: what Hoard offers and asks for."""
    return {
        "plugin_kind": "service",
        "service": {"public_id": PUBLIC_ID, "protocol": PROTOCOL_VERSION, "scopes": SCOPES},
        "features": ["actions", "dashboards", "endpoints", "fields", "pages", "parts", "widgets"],
        "permissions": PERMISSIONS,
        "connections": CONNECTIONS,
        "webhooks": WEBHOOKS,
        "schedules": SCHEDULES,
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
