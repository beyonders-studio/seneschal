"""Listings: a store's products and variants, each linked to the item it
sells, by SKU, then title, then the store's id, or by hand; and noting when
an item's stock changes, so that each of its listings is pushed what is
available (``hoard.push``)."""

from __future__ import annotations

from collections.abc import Iterable

from hoard.channels import StoreProduct
from hoard.records import Store
from seneschal_core.db import WriteSession


async def note_changed(db: WriteSession, item_id: int) -> None:
    """Note that an item's stock changed, so that each of its listings is
    pushed what is available once the change commits."""


async def match(db: WriteSession, store: Store, products: Iterable[StoreProduct]) -> None:
    """Keep a listing for each of ``products``' variants, linking each not yet
    linked to an item by SKU, then title, then the store's id."""
    raise NotImplementedError
