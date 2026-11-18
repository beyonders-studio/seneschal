"""Only the app's own pages change things with its cookies.

A browser sends the app's session cookie with a request another site's page
starts, wherever the cookie allows it; a framed page's cookie is ``SameSite=None``
in production, so it allows every site. An unsafe request (anything but
``GET``, ``HEAD`` and ``OPTIONS``) that carries one of the app's cookies is
therefore refused unless the browser says it came from the app's own origin:

* ``Sec-Fetch-Site: same-origin``; or
* an ``Origin`` among the app's own; or
* where the browser sends no ``Sec-Fetch-Site``, an ``Origin`` on the address
  the request was made to.

A request without an ``Origin`` is refused. One that carries none of the app's
cookies is left alone: Initiative's calls to the plug-in platform's routes
authenticate with the tokens they carry.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from http.cookies import CookieError, SimpleCookie

from starlette.types import ASGIApp, Receive, Scope, Send

from seneschal_core.api import ErrorCode

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SameOriginOnly:
    """Refuses an unsafe request that carries one of ``cookie_names`` unless it
    came from one of ``origins`` (``https://hoard.example.com``) or the
    address it was made to."""

    def __init__(
        self, app: ASGIApp, *, cookie_names: Collection[str], origins: Collection[str]
    ) -> None:
        self.app = app
        self.cookie_names = frozenset(cookie_names)
        self.origins = frozenset(origin.rstrip("/") for origin in origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope["headers"]
        }
        if not self._carries_cookie(headers.get("cookie")) or self._same_origin(headers):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": ErrorCode.CROSS_SITE_REQUEST.value}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _carries_cookie(self, header: str | None) -> bool:
        if not header:
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(header)
        except CookieError:
            # A cookie header that does not parse may still carry ours.
            return True
        return not self.cookie_names.isdisjoint(cookies.keys())

    def _same_origin(self, headers: dict[str, str]) -> bool:
        fetch_site = headers.get("sec-fetch-site")
        if fetch_site == "same-origin":
            return True
        origin = headers.get("origin", "").rstrip("/")
        if not origin or origin == "null":
            return False
        if origin in self.origins:
            return True
        host = headers.get("host")
        return fetch_site is None and host is not None and origin.split("://", 1)[-1] == host
