"""Hoard's MCP server: which of Hoard's routes an assistant may call, as the
person who signed it in (``seneschal_core.mcp``).

The surface is chosen, and a new route is not a tool until it is named here:

* **Reads:** every ``GET`` of the areas in :data:`READ_TAGS`, the records and
  reports a question about stock or production is answered from, and the
  settings that name their ids, less :data:`NOT_READ`.
* **Writes:** :data:`WRITES` exactly. Make and edit the catalogue, purchases,
  runs and orders, and record what happened to stock: a purchase line
  received, a run started or completed, an adjustment, a transfer, a stocktake
  counted. Nothing that deletes, reopens, cancels, unreceives or ships; nothing
  that changes settings, a store's sync or a hold; no import or export. Those
  are done in Hoard's pages.

What each call may see and do is still what the person may, where they chose
to open Hoard when they signed the assistant in.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.utilities.openapi.models import HTTPRoute

from seneschal_core.mcp import build_server

#: The areas whose reads are tools.
READ_TAGS = frozenset(
    {
        "adjustments",
        "bases",
        "contacts",
        "declarations",
        "documents",
        "holds",
        "listings",
        "lots",
        "materials",
        "members",
        "orders",
        "products",
        "properties",
        "purchases",
        "recipes",
        "reports",
        "runs",
        "search",
        "settings",
        "stocktakes",
        "trace",
    }
)

#: Reads in those areas that are not tools: a picture's or an export's bytes,
#: which a tool's answer cannot carry, and the search box's suggestions, which
#: answer for a text field rather than a question.
NOT_READ = frozenset(
    {
        "read_product_image",
        "list_product_images",
        "export_recall",
        "suggest_records",
    }
)

#: The writes that are tools.
WRITES = frozenset(
    {
        # The catalogue.
        "create_material",
        "update_material",
        "create_base",
        "update_base",
        "create_product",
        "update_product",
        "create_product_variation",
        "update_product_variation",
        "create_recipe",
        "update_recipe",
        "preview_recipe",
        "create_contact",
        # Buying.
        "create_purchase",
        "update_purchase",
        "create_purchase_line",
        "update_purchase_line",
        "receive_purchase_line",
        # Making.
        "preview_run",
        "create_run",
        "update_run",
        "update_run_line",
        "start_run",
        "complete_production_run",
        # Stock.
        "create_adjustment",
        "create_transfer",
        "create_stocktake",
        "confirm_stocktake_row",
        # Selling.
        "create_order",
        "update_order",
        "create_order_line",
        "update_order_line",
    }
)

INSTRUCTIONS = """\
Hoard keeps a small maker's inventory and production: materials bought from
suppliers, bases and products made from them by recipes in production runs,
the lots each makes, and orders that sell them. Stock is counted per item, per
location and per lot.

You act as the person who signed you in, in the community and place they chose
then; you see and change only what they may. Costs and prices are left out
for a person who may not see them.

Find a record by name or code with search_records, then read it by id. The
settings' lists (units, types, categories, locations, reasons) give the ids
records refer to. An update_ tool on a whole record (a material, base,
product or recipe) replaces it: read it first and send it back whole with
your change. Preview a recipe or a run before you make one.
"""


def is_tool(route: HTTPRoute) -> bool:
    """Whether a route of Hoard's API is a tool."""
    name = route.operation_id or ""
    if route.method == "GET":
        return name not in NOT_READ and not READ_TAGS.isdisjoint(route.tags)
    return name in WRITES


def build_mcp_server(app: FastAPI) -> FastMCP:
    """Hoard's MCP server, from Hoard's app once its routers are included."""
    return build_server(app, name="Hoard", instructions=INSTRUCTIONS, tools=is_tool)
