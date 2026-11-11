"""The API schema the frontend generates its client from."""

from __future__ import annotations

from collections import Counter

from hoard.openapi import openapi_schema


def test_each_call_is_named_once_after_its_function():
    schema = openapi_schema()

    names = Counter(
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
    )
    assert [name for name, count in names.items() if count > 1] == []
    assert schema["paths"]["/api/v1/auth/me"]["get"]["operationId"] == "read_me"
