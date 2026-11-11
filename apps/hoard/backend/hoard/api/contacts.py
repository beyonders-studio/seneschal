"""Suppliers among the community's contacts in Initiative, read and added through
:attr:`~seneschal_core.api.Plugin.api`."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/contacts", tags=["contacts"])
