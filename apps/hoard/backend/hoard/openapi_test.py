"""The API schema the frontend generates its client from."""

from __future__ import annotations

from hoard.openapi import openapi_schema


def test_the_schema_names_each_call_the_pages_make():
    schema = openapi_schema()

    calls = {
        (path, method): operation["operationId"]
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
    }
    assert calls == {
        ("/api/v1/healthz", "get"): "healthz",
        ("/api/v1/auth/handoff/exchange", "post"): "exchange_handoff",
        ("/api/v1/auth/me", "get"): "read_me",
    }
