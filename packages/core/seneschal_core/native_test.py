"""The web bundle an app's phone app takes over the air."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient

from seneschal_core.native import DOWNLOAD_PATH, native_router


async def ask(ota_dir: Path, path: str):
    app = FastAPI()
    app.include_router(native_router(ota_dir, "1.2.3"), prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_a_signed_bundle_is_served_with_its_statement(tmp_path: Path):
    (tmp_path / "bundle.zip").write_bytes(b"PK zip")
    statement = json.dumps({"v": 2, "app": "hoard", "version": "1.2.3-dev.abc1234"})
    (tmp_path / "statement.json").write_text(statement)
    (tmp_path / "statement.sig").write_text("c2lnbmVk\n")

    manifest = await ask(tmp_path, "/api/v1/native/bundle/manifest")
    download = await ask(tmp_path, DOWNLOAD_PATH)

    assert manifest.json() == {
        "version": "1.2.3-dev.abc1234",
        "url": DOWNLOAD_PATH,
        "statement": statement,
        "signature": "c2lnbmVk",
    }
    assert download.content == b"PK zip"
    assert "immutable" in download.headers["cache-control"]


async def test_an_unsigned_bundle_is_served_without_one(tmp_path: Path):
    (tmp_path / "bundle.zip").write_bytes(b"PK zip")

    manifest = (await ask(tmp_path, "/api/v1/native/bundle/manifest")).json()

    assert (manifest["version"], manifest["statement"], manifest["signature"]) == (
        "1.2.3",
        None,
        None,
    )


async def test_an_image_without_a_bundle_has_none(tmp_path: Path):
    assert (await ask(tmp_path, "/api/v1/native/bundle/manifest")).status_code == 404
    assert (await ask(tmp_path, DOWNLOAD_PATH)).status_code == 404
