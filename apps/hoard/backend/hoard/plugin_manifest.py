"""What Hoard tells Initiative it is: its plug-in manifest.

Hoard is a container plug-in. Its manifest names its service, the scopes it
asks a community to grant, and its pages: Stock, Production and Reports inside
each initiative it is placed in, and Settings for the whole community, which
only the community's admins open. Each page opens on Hoard's implicit ``use``
permission. ``scripts/export_plugin.py`` writes it for the plug-in SDK, which
checks it and builds its registry source.
"""

from __future__ import annotations

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

#: Each page: its id, the path Hoard serves it at, where it renders, and its name.
PAGES = [
    ("stock", "/stock", "initiative", "Stock"),
    ("production", "/production", "initiative", "Production"),
    ("reports", "/reports", "initiative", "Reports"),
    ("settings", "/settings", "community", "Settings"),
]


def definition() -> dict[str, Any]:
    """The manifest: what Hoard offers and asks for."""
    return {
        "plugin_kind": "service",
        "service": {"public_id": PUBLIC_ID, "protocol": PROTOCOL_VERSION, "scopes": SCOPES},
        "features": ["pages"],
        "pages": [
            {"id": page, "path": path, "scopes": [scope], "name": {"en": name}}
            for page, path, scope, name in PAGES
        ],
    }


def built_plugin() -> dict[str, Any]:
    """Hoard as the plug-in SDK's ``initiative-plugin build --manifest`` reads it."""
    return {"publicId": PUBLIC_ID, "uid": UID, "name": NAME, "manifest": definition()}
