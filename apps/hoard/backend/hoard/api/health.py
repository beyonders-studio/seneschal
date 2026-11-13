"""Whether Hoard is up, and whether it is ready to serve."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from seneschal_core.api import PluginDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Whether the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(plugin: PluginDep) -> dict[str, str]:
    """Whether Hoard reaches its database, without which it serves nothing."""
    try:
        async with plugin.database.session() as db:
            await db.execute(text("SELECT 1"))
    except Exception as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "DATABASE_UNAVAILABLE") from error
    return {"status": "ok"}
