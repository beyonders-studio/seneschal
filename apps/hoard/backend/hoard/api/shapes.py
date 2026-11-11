"""Shapes every area's answers share."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel


class Page[T](BaseModel):
    """One page of a list: its items, and how many the whole list holds."""

    items: list[T]
    total: int


@dataclass(frozen=True)
class PageQuery:
    """Which page of a list is asked for: ``limit`` items after the first
    ``offset``."""

    offset: int
    limit: int


def _page_query(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PageQuery:
    return PageQuery(offset=offset, limit=limit)


PageDep = Annotated[PageQuery, Depends(_page_query)]
