"""What Hoard tells Initiative it is: its plug-in manifest.

Hoard is a container plug-in. Its manifest names its service, the scopes it
asks a community to grant, and its pages: Stock, Production and Reports inside
each initiative it is placed in, and Settings for the whole community, which
only the community's admins open. Each page opens on Hoard's implicit ``use``
permission; seeing costs and prices takes :data:`SEE_COSTS` as well.

For dashboards it declares two reads (``hoard.endpoints`` answers them), a
widget drawing each (the plain scripts in ``widgets/``, which Initiative runs
in its widget sandbox), and one dashboard arranging both, which publishing
Hoard lists on its own.

``scripts/export_plugin.py`` writes it for the plug-in SDK, which checks it and
builds its registry source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
        "features": ["dashboards", "endpoints", "pages", "widgets"],
        "permissions": PERMISSIONS,
        "pages": [
            {"id": page, "path": path, "scopes": [scope], "name": {"en": name}}
            for page, path, scope, name in PAGES
        ],
        "endpoints": ENDPOINTS,
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
