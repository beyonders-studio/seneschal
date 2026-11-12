"""Initiative's files, searched for the member viewing the page, so that only
files they may open are found."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/files", tags=["files"])
