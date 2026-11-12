"""Access tokens stores give Hoard, kept in memory, each until shortly before
it expires, and asked for again after that or once forgotten."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

#: How long before a token expires it is asked for again.
EXPIRY_SKEW_SECONDS = 60.0

#: Asks the store for a token: answers it, and how many seconds it lasts.
Issue = Callable[[], Awaitable[tuple[str, float]]]


class Tokens:
    """Tokens by key, such as a store's address and its app's client ID."""

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get(self, key: str, issue: Issue) -> str:
        """The token kept for ``key`` while it is fresh, else one ``issue`` asks for."""
        async with self._locks[key]:
            kept = self._tokens.get(key)
            if kept is not None and time.monotonic() < kept[1]:
                return kept[0]
            token, lifetime = await issue()
            self._tokens[key] = (token, time.monotonic() + lifetime - EXPIRY_SKEW_SECONDS)
            return token

    def forget(self, key: str) -> None:
        """Ask for a new token next time, as when the store refused this one."""
        self._tokens.pop(key, None)


#: The tokens Hoard holds.
TOKENS = Tokens()
