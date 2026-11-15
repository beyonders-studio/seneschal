"""Properties people add to a kind of record (``hoard.records.PropertyDefinition``):
the options of a select."""

from __future__ import annotations

from typing import Any

from hoard.records import PropertyDefinition

#: The longest an option's value is.
OPTION_VALUE_LENGTH = 64


def add_option(definition: PropertyDefinition, label: str) -> dict[str, Any]:
    """The option of a select labelled ``label``, ignoring case, added to
    ``definition`` with a value of its own where it has none."""
    options = list(definition.options or [])
    label = label.strip()
    for option in options:
        if str(option["label"]).strip().lower() == label.lower():
            return option
    base = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in label.lower())
    base = base.strip("_")[:OPTION_VALUE_LENGTH] or "option"
    taken = {option["value"] for option in options}
    value = next(
        slug for slug in (base, *(f"{base[:58]}-{n}" for n in range(2, 1000))) if slug not in taken
    )
    option = {"value": value, "label": label[:100], "color": None}
    definition.options = [*options, option]
    return option
