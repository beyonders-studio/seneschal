"""Whether Hoard is up."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Whether the process is up."""
    return {"status": "ok"}
