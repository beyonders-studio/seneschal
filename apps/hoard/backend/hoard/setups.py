"""Starting setups: what a kind of business starts Hoard with, kept as data in
``setups/<key>.json``. The first time Hoard is opened, an admin is asked which
kind of business this is: the answer adds its setup, or, for a business Hoard
has none for, starts blank. Either way it is asked once.

A setup brings units, categories (its kinds of item, each for the kinds of
record it sorts, with the rule for which of its lots goes first), properties
of records, each with its key, adjustment reasons for waste, hold reasons,
each with its key, and the word for a base. Adding one adds what the
organization does not have and changes nothing it does, so adding it again
changes nothing. Only an organization that has not yet begun adds one
(:func:`started`): a setup is chosen once, never added later.

Everything a setup adds is named in the words of the member who adds it: the
page sends the words for each of the setup's keys (:meth:`Setup.words`),
which its locale holds, and people rename them as they like afterwards. A
property and a hold reason keep their keys, so adding the setup again finds
them however they were renamed; a unit, a category or an adjustment reason is
found by its name. A hold reason that is one of the defaults keeps the name
the setup gives it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from hoard.records import (
    AdjustmentReason,
    Category,
    CategoryKind,
    HoldReason,
    OrganizationSettings,
    Picking,
    PropertyDefinition,
    PropertyType,
    RecordKind,
    Unit,
)
from seneschal_core.db import WriteSession

SETUPS_DIR = Path(__file__).parent / "setups"

#: The word every setup that keeps it leaves alone: pages say the translated
#: word for a base until someone names it.
BASE = "base"


class SetupError(StrEnum):
    #: The words sent leave out one of the setup's keys.
    WORDS_MISSING = "WORDS_MISSING"
    #: The organization has begun already, with a setup or blank.
    SETUP_STARTED = "SETUP_STARTED"


class SetupRefused(Exception):
    def __init__(self, code: SetupError) -> None:
        super().__init__(code.value)
        self.code = code


@dataclass(frozen=True)
class SetupCategory:
    key: str
    kinds: tuple[CategoryKind, ...]
    #: Which of its items' lots goes first.
    picking: Picking


@dataclass(frozen=True)
class SetupProperty:
    key: str
    kind: RecordKind
    type: PropertyType
    #: A select's options, by value.
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Setup:
    key: str
    #: What it calls a base: :data:`BASE`, or a word of its own.
    base_word: str
    units: tuple[str, ...]
    categories: tuple[SetupCategory, ...]
    properties: tuple[SetupProperty, ...]
    #: Why stock is thrown away.
    reasons: tuple[str, ...]
    #: Why a lot is put on hold, by key.
    hold_reasons: tuple[str, ...]

    def words(self) -> list[str]:
        """The keys whose words the member adding it sends."""
        return [
            *(["base_word"] if self.base_word != BASE else []),
            *(f"units.{unit}" for unit in self.units),
            *(f"categories.{category.key}" for category in self.categories),
            *(
                word
                for each in self.properties
                for word in (
                    f"properties.{each.key}",
                    *(f"options.{each.key}.{option}" for option in each.options),
                )
            ),
            *(f"reasons.{reason}" for reason in self.reasons),
            *(f"hold_reasons.{reason}" for reason in self.hold_reasons),
        ]


def _setup(path: Path) -> Setup:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Setup(
        key=path.stem,
        base_word=data["base_word"],
        units=tuple(data["units"]),
        categories=tuple(
            SetupCategory(
                key=each["key"],
                kinds=tuple(CategoryKind(kind) for kind in each["kinds"]),
                picking=Picking(each["picking"]),
            )
            for each in data["categories"]
        ),
        properties=tuple(
            SetupProperty(
                key=each["key"],
                kind=RecordKind(each["kind"]),
                type=PropertyType(each["type"]),
                options=tuple(each.get("options", ())),
            )
            for each in data["properties"]
        ),
        reasons=tuple(data["reasons"]),
        hold_reasons=tuple(data["hold_reasons"]),
    )


@cache
def setups() -> dict[str, Setup]:
    """Every setup Hoard offers, by key."""
    return {path.stem: _setup(path) for path in sorted(SETUPS_DIR.glob("*.json"))}


async def added(db: AsyncSession, setup: Setup) -> bool:
    """Whether the organization has added ``setup``: it holds one of its properties."""
    found = select(func.count()).where(
        col(PropertyDefinition.key).in_([each.key for each in setup.properties])
    )
    return (await db.exec(found)).one() > 0


async def started(db: AsyncSession) -> bool:
    """Whether the organization has begun on its own: an admin chose to start
    blank, or it has a unit, which whatever holds stock is counted in."""
    if (await db.exec(select(OrganizationSettings.started_blank))).first():
        return True
    return (await db.exec(select(func.count()).select_from(Unit))).one() > 0


async def start_blank(db: WriteSession) -> None:
    """Record that an admin chose to start without a setup."""
    settings = (await db.exec(select(OrganizationSettings))).first() or OrganizationSettings(
        organization_id=db.organization_id
    )
    settings.started_blank = True
    db.add(settings)
    await db.flush()


async def add(db: WriteSession, setup: Setup, words: Mapping[str, str]) -> None:
    """Add what ``setup`` brings that the organization lacks, named by ``words``."""
    if missing := set(setup.words()) - words.keys():
        raise SetupRefused(SetupError.WORDS_MISSING) from KeyError(sorted(missing))
    organization = {"organization_id": db.organization_id}
    rows = [
        (Unit, [{"name": words[f"units.{unit}"]} for unit in setup.units]),
        (
            Category,
            [
                {
                    "kind": kind,
                    "name": words[f"categories.{category.key}"],
                    "picking": category.picking,
                }
                for category in setup.categories
                for kind in category.kinds
            ],
        ),
        (
            PropertyDefinition,
            [
                {
                    "kind": each.kind,
                    "key": each.key,
                    "name": words[f"properties.{each.key}"],
                    "type": each.type,
                    "options": [
                        {"value": option, "label": words[f"options.{each.key}.{option}"]}
                        for option in each.options
                    ]
                    or None,
                }
                for each in setup.properties
            ],
        ),
        (AdjustmentReason, [{"name": words[f"reasons.{reason}"]} for reason in setup.reasons]),
        (
            HoldReason,
            [
                {"key": reason, "name": words[f"hold_reasons.{reason}"]}
                for reason in setup.hold_reasons
            ],
        ),
    ]
    for model, values in rows:
        if values:
            await db.execute(
                insert(model)
                .values([{**organization, **row} for row in values])
                .on_conflict_do_nothing()
            )
    if setup.base_word != BASE:
        settings = (await db.exec(select(OrganizationSettings))).first()
        if settings is None:
            settings = OrganizationSettings(organization_id=db.organization_id)
        if settings.base_word is None:
            settings.base_word = words["base_word"]
            db.add(settings)
            await db.flush()
