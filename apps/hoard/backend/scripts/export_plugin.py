"""Write Hoard as the plug-in SDK reads it, for ``initiative-plugin build
--manifest <file>``, which checks it against the plug-in contract and builds the
registry source a release attaches (``.github/workflows/publish.yml``).

Only the prose a reader sees before installing is written here. The manifest is
``hoard.plugin_manifest``'s, the version is ``apps/hoard/VERSION``, the release
notes are that version's ``CHANGELOG.md`` section, and the image is the one the
release pushed, by digest.

    uv run python apps/hoard/backend/scripts/export_plugin.py plugin.json \\
        --image ghcr.io/beyonders-studio/hoard@sha256:…
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from hoard.plugin_manifest import built_plugin

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent

#: The listing's picture, relative to the backend.
AVATAR = "assets/avatar.png"

SUMMARY = (
    "Inventory and production for makers: stock, recipes, production runs, "
    "lots and orders, kept in step with your store."
)
DESCRIPTION = (
    "Hoard keeps track of what your business makes and sells.\n\n"
    "Record materials and their suppliers, receive purchases into lots, and "
    "make products from recipes in production runs, with costs that follow "
    "every movement. Trace any lot back to its suppliers and forward to every "
    "order it reached, put lots on hold, and recall them in one action.\n\n"
    "Connect a Shopify store to copy its orders in and keep what it shows as "
    "available in step with your stock. Runs and purchases can be tracked on "
    "your project's tasks, and Hoard's reports and widgets sit on your "
    "dashboards."
)

#: The oldest Initiative release Hoard runs on: the one that lets a plug-in
#: add a community contact, as Hoard adds suppliers.
MIN_APP_VERSION = "0.79.0"

#: How a deployment runs Hoard from a package, with no code of its own for it
#: (``registration.runtime``). Every name is one Hoard reads
#: (``hoard.core.config``); the deployment supplies the values. Nothing here
#: sizes, networks or privileges the container: those stay the deployment's.
RUNTIME: dict[str, Any] = {
    "name": "hoard",
    "port": 8300,
    "health": {"ready": "/api/v1/readyz", "live": "/api/v1/healthz", "startupSeconds": 180},
    "stopGraceSeconds": 30,
    # The image's command runs the migrations before serving.
    "migratesOnStart": True,
    # Hoard's pages are framed by Initiative, at Hoard's own origin.
    "pages": True,
    "facts": {
        "HOARD_APP_ORIGIN": "page_origin",
        "HOARD_EMBED_PARENT_ORIGIN": "app_origin",
        "HOARD_INITIATIVE_API_URL": "app_api_internal",
        "HOARD_INITIATIVE_PUBLIC_URL": "app_api_public",
        "INITIATIVE_ENTITLEMENT_ISSUER_KEY_PEM": "entitlement_issuer_key",
    },
    "signingKey": {"env": "HOARD_PLUGIN_PRIVATE_KEY_PEM"},
    "database": {
        "minMajor": 17,
        "dsnScheme": "postgresql+asyncpg",
        # The owner runs the migrations; the app serves, under row security.
        "roles": [
            {"id": "owner", "login": "hoard_owner", "env": "HOARD_DATABASE_URL"},
            {"id": "app", "login": "hoard_app", "env": "HOARD_DATABASE_URL_APP"},
        ],
        "provisionSql": "/app/apps/hoard/backend/scripts/provision-roles.sql",
    },
    "secrets": [
        {
            "id": "session-private-key",
            "kind": "ed25519_private_key_pem",
            "env": "HOARD_SESSION_PRIVATE_KEY_PEM",
        },
    ],
}


def release_notes(version: str) -> str | None:
    """This version's ``CHANGELOG.md`` section, verbatim; None when it is empty."""
    changelog = (APP_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.DOTALL | re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section to release")
    return match.group(1).strip() or None


def listed_plugin(image: str, version: str) -> dict[str, Any]:
    """Hoard with its listing at ``version``, running ``image``."""
    listing: dict[str, Any] = {
        "publisher": "beyonders-studio",
        "summary": SUMMARY,
        "description": DESCRIPTION,
        "avatar": AVATAR,
        "version": version,
        "minAppVersion": MIN_APP_VERSION,
        "image": image,
        "runtime": RUNTIME,
    }
    notes = release_notes(version)
    if notes is not None:
        listing["releaseNotes"] = notes
    return {**built_plugin(), "listing": listing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="where to write the plug-in")
    parser.add_argument("--image", required=True, help="the image the release pushed, by digest")
    args = parser.parse_args()
    version = (APP_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    args.out.write_text(
        json.dumps(listed_plugin(args.image, version), indent=2, ensure_ascii=False) + "\n"
    )
    print(f"wrote {args.out} at version {version}")


if __name__ == "__main__":
    main()
