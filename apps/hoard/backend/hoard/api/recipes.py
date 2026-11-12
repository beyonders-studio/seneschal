"""Recipes: what a batch of a variation or a base is made from, and what a bundle holds."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/recipes", tags=["recipes"])
