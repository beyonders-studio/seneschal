"""The web bundle an app's phone app takes over the air.

An app's image carries its phone app's pages, built with it, as a zip and a
statement of it (``packages/native/bin/sign-ota.mjs``): the app, the
version, the zip's SHA-256 and the oldest phone app it runs on, signed with
the project's update key. The phone app asks for the manifest when it opens,
checks the statement against the keys it was built with, and downloads the
zip only when the signature holds and the statement names it. An image built
without the key serves its bundle unsigned, and no phone app installs it.

Nothing here is a person's: the routes answer anyone, as the pages
themselves do.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

#: Where the zip is served from, under the app's API.
DOWNLOAD_PATH = "/api/v1/native/bundle/download"


class BundleManifest(BaseModel):
    """The bundle the image carries, and its signed statement when it has one."""

    version: str
    #: The zip, on the app's own origin.
    url: str
    #: The statement, as signed: JSON text, verified before it is read.
    statement: str | None
    #: Its signature, ECDSA P-256 over SHA-256, IEEE P1363, base64.
    signature: str | None


def native_router(ota_dir: Path, version: str) -> APIRouter:
    """The routes serving ``ota_dir``'s bundle (``bundle.zip``, and
    ``statement.json`` and ``statement.sig`` when it was signed), whose
    version is the app's ``version`` unless its statement says another."""
    router = APIRouter(prefix="/native/bundle", tags=["native"])
    bundle = ota_dir / "bundle.zip"
    statement_file = ota_dir / "statement.json"
    signature_file = ota_dir / "statement.sig"

    @router.get("/manifest", responses={404: {"description": "No bundle in this image"}})
    async def read_bundle_manifest() -> BundleManifest:
        """The phone app's bundle this image carries."""
        if not bundle.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        signed = statement_file.is_file() and signature_file.is_file()
        statement = statement_file.read_text() if signed else None
        stated = json.loads(statement).get("version") if statement else None
        return BundleManifest(
            version=stated if isinstance(stated, str) else version,
            url=DOWNLOAD_PATH,
            statement=statement,
            signature=signature_file.read_text().strip() if signed else None,
        )

    @router.get("/download", include_in_schema=False)
    async def download_bundle() -> FileResponse:
        if not bundle.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(
            bundle,
            media_type="application/zip",
            # A bundle's content never changes under its version.
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    return router
