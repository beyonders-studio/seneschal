"""Hoard's web app: its API under ``/api/v1``, the plug-in platform's routes,
and its built pages, all from one origin."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

from hoard import plugin_manifest
from hoard.core.config import BACKEND_ROOT, Settings, get_settings
from seneschal_core.api import Plugin, install
from seneschal_core.auth import router as auth_router
from seneschal_core.db import Database
from seneschal_core.initiative import Initiative
from seneschal_core.initiative_api import InitiativeApi
from seneschal_core.platform import ORGANIZATION_HOOKS, platform_router
from seneschal_core.sessions import Sessions

#: The first path segments that are never a page.
_NOT_PAGES = frozenset({"api", "v1", ".well-known"})


def plugin_for(settings: Settings) -> Plugin:
    return Plugin(
        database=Database(settings.database_url_app),
        initiative=Initiative(
            api_url=settings.initiative_api_url,
            public_id=plugin_manifest.PUBLIC_ID,
            listing_uid=plugin_manifest.UID,
            statement_key_pem=settings.entitlement_issuer_key_pem,
        ),
        api=InitiativeApi(
            api_url=settings.initiative_api_url,
            public_id=plugin_manifest.PUBLIC_ID,
            key_pem=settings.plugin_private_key_pem,
        ),
        sessions=Sessions(
            app="hoard",
            private_key_pem=settings.session_private_key_pem,
            cookie_secure=settings.cookie_secure,
        ),
    )


def _version() -> str:
    version = BACKEND_ROOT.parent / "VERSION"
    return version.read_text().strip() if version.is_file() else "0.0.0"


def _operation_id(route: APIRoute) -> str:
    """The route's function name, which names the generated client's call."""
    return route.name


health = APIRouter(tags=["health"])


@health.get("/healthz")
async def healthz() -> dict[str, str]:
    """Whether the process is up."""
    return {"status": "ok"}


def create_app(settings: Settings | None = None, plugin: Plugin | None = None) -> FastAPI:
    settings = settings or get_settings()
    plugin = plugin or plugin_for(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await plugin.start()
        try:
            yield
        finally:
            await plugin.stop()

    app = FastAPI(
        title="Hoard",
        version=_version(),
        lifespan=lifespan,
        # The schema is exported at build time (scripts/export_openapi.py) and
        # not served.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    install(app, plugin)

    api = APIRouter(prefix="/api/v1")
    api.include_router(health)
    api.include_router(auth_router)
    app.include_router(api)
    app.include_router(platform_router(hooks=ORGANIZATION_HOOKS, endpoints={}))

    static = settings.static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def page(path: str) -> FileResponse:
        """A built file, or the pages' entry for any other path."""
        if path.split("/", 1)[0] in _NOT_PAGES:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        asset = (static / path).resolve()
        if not asset.is_relative_to(static):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if asset.is_file():
            return FileResponse(asset)
        index = static / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
