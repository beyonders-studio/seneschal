"""Reading what a barcode scan produced: a GS1 label, or a plain code.

A GS1 label carries several values at once, each behind its Application
Identifier (AI): (01) the GTIN of the product, (10) its lot, (17) its expiry,
(21) its serial, (00) the SSCC of a pallet, and so on. Scanners and cameras
hand it over in several forms, and :func:`read_scan` reads each:

- an element string, ``0100012345678905`` ``10L24-118`` ``<GS>`` ``17270630``,
  where the GS character (ASCII 29, the barcode's FNC1) ends a field whose
  length isn't fixed; often led by a symbology identifier (``]C1`` GS1-128,
  ``]d2`` GS1 DataMatrix, ``]Q3`` GS1 QR, ``]e0`` GS1 DataBar, ``]J1`` GS1
  DotCode);
- the human-readable form printed under the bars,
  ``(01)00012345678905(10)L24-118(17)270630``;
- a GS1 Digital Link, ``https://id.gs1.org/01/00012345678905/10/L24-118?17=270630``,
  on any host;
- a retail barcode alone (EAN-8, UPC-A, EAN-13, ITF-14): a GTIN.

Anything else is a :class:`PlainScan`: an app's own code, such as a SKU or a
lot number.

**A field whose length isn't fixed runs to the next GS, or to the end.** Where
a scanner drops the GS character, a lot followed by another field reads as one
long lot. Most labels put the lot last for this reason; when the text after a
lot could itself be read as GS1 fields, the scan says it is ``ambiguous``
rather than guess.

Dates are GS1's YYMMDD: the century is the one that puts the year nearest
today (from 49 years back to 50 ahead), and a day of ``00`` means the month's
last day.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, unquote, urlsplit

#: The GS character, which a GS1 barcode's FNC1 becomes.
GS = "\x1d"

#: Symbology identifiers that announce a GS1 element string.
_GS1_SYMBOLOGIES = ("]C1", "]d2", "]Q3", "]e0", "]J1")
#: Any symbology identifier: ``]`` and two characters.
_SYMBOLOGY = re.compile(r"^\][A-Za-z][0-9A-Za-z]")

#: How many digits an AI has, by its first two (GS1 General Specifications).
_AI_DIGITS: dict[str, int] = {
    **dict.fromkeys(["00", "01", "02", "03", "04"], 2),
    **{f"{n:02}": 2 for n in range(10, 23)},
    "23": 3,
    "24": 3,
    "25": 3,
    "30": 2,
    **dict.fromkeys(["31", "32", "33", "34", "35", "36"], 4),
    "37": 2,
    "39": 4,
    "40": 3,
    "41": 3,
    "42": 3,
    "43": 4,
    "70": 4,
    "71": 4,
    "72": 4,
    "80": 4,
    "81": 4,
    "82": 4,
    **{f"{n}": 2 for n in range(90, 100)},
}

#: The AIs whose length is fixed, so need no GS after them: the whole field,
#: AI and value, by the AI's first two digits (GS1's predefined lengths).
_PREDEFINED: dict[str, int] = {
    "00": 20,
    "01": 16,
    "02": 16,
    "03": 16,
    "04": 18,
    **{f"{n}": 8 for n in range(11, 20)},
    "20": 4,
    **dict.fromkeys(["31", "32", "33", "34", "35", "36"], 10),
    "41": 16,
}

#: The longest value of an AI whose length isn't fixed; 30 for any not named.
_MAX_VARIABLE: dict[str, int] = {
    "10": 20,
    "21": 20,
    "22": 20,
    "30": 8,
    "37": 8,
    "254": 20,
    "420": 20,
    "421": 12,
    **{f"{n}": 90 for n in range(91, 100)},
}

#: Characters a GS1 value may hold (the GS1 AI encodable character set 82).
_VALUE = re.compile(r"^[!\"%-?A-Z_a-z]+$")
_DIGITS = re.compile(r"^[0-9]+$")

#: Digital Link names for the AIs, as its paths and queries may spell them.
_LINK_NAMES = {
    "gtin": "01",
    "sscc": "00",
    "lot": "10",
    "ser": "21",
    "exp": "17",
    "expdt": "17",
    "bbd": "15",
    "proddate": "11",
}


@dataclass(frozen=True)
class PlainScan:
    """A scan that isn't GS1: the text itself, trimmed."""

    text: str


@dataclass(frozen=True)
class Gs1Scan:
    """A GS1 label's values, by AI in the order they came, and what is
    wrong with them, if anything."""

    elements: dict[str, str]
    #: The scan as it came, trimmed: an app's own code that happens to read
    #: as a GTIN (an 8-, 12- or 13-digit SKU) can still be looked up by it.
    raw: str = ""
    #: ``check_digit``, ``date``, ``unknown_ai``, ``too_long``, ``format``
    #: and ``ambiguous``: the label is read, but something in it can't be
    #: trusted as read.
    problems: tuple[str, ...] = field(default=())

    @property
    def gtin(self) -> str | None:
        """The product's GTIN, as 14 digits."""
        return self.elements.get("01") or self.elements.get("02")

    @property
    def lot(self) -> str | None:
        return self.elements.get("10")

    @property
    def serial(self) -> str | None:
        return self.elements.get("21")

    @property
    def sscc(self) -> str | None:
        return self.elements.get("00")

    @property
    def expires_on(self) -> date | None:
        return _date(self.elements.get("17"))

    @property
    def best_before(self) -> date | None:
        return _date(self.elements.get("15"))

    @property
    def produced_on(self) -> date | None:
        return _date(self.elements.get("11"))

    @property
    def count(self) -> int | None:
        value = self.elements.get("30") or self.elements.get("37")
        return int(value) if value is not None and _DIGITS.match(value) else None

    @property
    def ambiguous(self) -> bool:
        return "ambiguous" in self.problems


Scan = Gs1Scan | PlainScan


def read_scan(text: str, *, gs1: bool | None = None) -> Scan:
    """What ``text``, a scan, says. ``gs1`` is what the scanner knew of the
    symbol: true for a GS1 symbol, false for a plain one, None when it didn't
    say."""
    text = text.strip("\r\n\t ")
    if not text:
        return PlainScan("")
    if text.lower().startswith(("http://", "https://")):
        link = _read_link(text)
        return _with_raw(link, text) if link is not None else PlainScan(text)
    symbology = _SYMBOLOGY.match(text)
    if symbology:
        marked = text.startswith(_GS1_SYMBOLOGIES)
        text = text[3:]
        if not marked:
            # EAN and UPC (]E0, ]E4) carry a GTIN without being GS1-marked.
            gtin = normalize_gtin(text) if gs1 is not False else None
            return Gs1Scan({"01": gtin}, raw=text) if gtin else PlainScan(text)
        gs1 = True
    text = text.lstrip(GS)
    if text.startswith("("):
        parsed = _read_bracketed(text)
        if parsed is not None:
            return _with_raw(parsed, text)
    if gs1 is False:
        return PlainScan(text)
    if (gtin := normalize_gtin(text)) is not None:
        return Gs1Scan({"01": gtin}, raw=text)
    if gs1 or GS in text or _looks_like_element_string(text):
        parsed = _read_element_string(text)
        if parsed is not None:
            return _with_raw(parsed, text)
    return PlainScan(text)


def _with_raw(scan: Gs1Scan, raw: str) -> Gs1Scan:
    return Gs1Scan(scan.elements, raw=raw, problems=scan.problems)


def normalize_gtin(text: str) -> str | None:
    """``text`` as a 14-digit GTIN when it is one (8, 12, 13 or 14 digits with
    a good check digit), else None."""
    text = text.strip()
    if len(text) not in (8, 12, 13, 14) or not _DIGITS.match(text):
        return None
    padded = text.zfill(14)
    return padded if check_digit_holds(padded) else None


def check_digit_holds(digits: str) -> bool:
    """Whether the last digit is the GS1 check digit of those before it."""
    if len(digits) < 2 or not _DIGITS.match(digits):
        return False
    body = digits[:-1]
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == int(digits[-1])


# --- the forms ---------------------------------------------------------------


def _looks_like_element_string(text: str) -> bool:
    """Whether unmarked text starts as a GS1 label does: a GTIN or an SSCC,
    with a good check digit, then more."""
    return bool(
        (text.startswith("01") and len(text) >= 16 and check_digit_holds(text[2:16]))
        or (text.startswith("00") and len(text) >= 20 and check_digit_holds(text[2:20]))
    )


def _ai_at(text: str, at: int) -> str | None:
    digits = _AI_DIGITS.get(text[at : at + 2])
    if digits is None:
        return None
    ai = text[at : at + digits]
    return ai if len(ai) == digits and _DIGITS.match(ai) else None


def _split(text: str) -> list[tuple[str, str]] | None:
    """The fields of an element string, in order; None when it isn't one."""
    fields: list[tuple[str, str]] = []
    at = 0
    while at < len(text):
        if text[at] == GS:
            at += 1
            continue
        ai = _ai_at(text, at)
        if ai is None:
            return None
        start = at + len(ai)
        fixed = _PREDEFINED.get(ai[:2])
        if fixed is not None:
            end = at + fixed
            if end > len(text):
                return None
        else:
            separator = text.find(GS, start)
            end = len(text) if separator < 0 else separator
        value = text[start:end]
        if not value:
            return None
        fields.append((ai, value))
        at = end
    return fields or None


def _read_element_string(text: str) -> Gs1Scan | None:
    fields = _split(text)
    if fields is None:
        return None
    problems: list[str] = []
    # A field that ran to the end without a GS may have swallowed the fields
    # that followed it, if the scanner dropped the separator.
    last_ai, last_value = fields[-1]
    if _PREDEFINED.get(last_ai[:2]) is None and any(
        (rest := _split(last_value[k:])) is not None and _all_known(rest)
        for k in range(1, len(last_value))
    ):
        problems.append("ambiguous")
    return _scan(fields, problems)


def _read_bracketed(text: str) -> Gs1Scan | None:
    """The form printed under the bars: ``(01)…(10)…``."""
    parts = re.findall(r"\((\d{2,4})\)([^()]*)", text)
    if not parts or "".join(f"({ai}){value}" for ai, value in parts) != text:
        return None
    return _scan([(ai, value.strip()) for ai, value in parts], [])


def _read_link(url: str) -> Gs1Scan | None:
    """A GS1 Digital Link: AIs and values as path pairs, and as a query."""
    parts = urlsplit(url)
    segments = [unquote(s) for s in parts.path.split("/") if s]
    fields: list[tuple[str, str]] = []
    # The identifiers are the path's last pairs: whatever comes before them is
    # the host's own path.
    for at in range(len(segments) - 1):
        key = _LINK_NAMES.get(segments[at].lower(), segments[at])
        if key in ("01", "00", "02") and _DIGITS.match(segments[at + 1]):
            pairs = segments[at:]
            if len(pairs) % 2:
                return None
            fields = [
                (_LINK_NAMES.get(pairs[i].lower(), pairs[i]), pairs[i + 1])
                for i in range(0, len(pairs), 2)
            ]
            break
    if not fields:
        return None
    for key, value in parse_qsl(parts.query):
        ai = _LINK_NAMES.get(key.lower(), key)
        if _DIGITS.match(ai) and _AI_DIGITS.get(ai[:2]) == len(ai):
            fields.append((ai, value))
    if not all(_DIGITS.match(ai) for ai, _ in fields):
        return None
    if fields[0][0] == "01" and len(fields[0][1]) in (8, 12, 13):
        fields[0] = ("01", fields[0][1].zfill(14))
    return _scan(fields, [])


# --- checking the fields -----------------------------------------------------


def _all_known(fields: list[tuple[str, str]]) -> bool:
    return all(not _field_problems(ai, value) for ai, value in fields)


def _field_problems(ai: str, value: str) -> list[str]:
    problems: list[str] = []
    if _AI_DIGITS.get(ai[:2]) != len(ai):
        return ["unknown_ai"]
    fixed = _PREDEFINED.get(ai[:2])
    if fixed is not None:
        if len(ai) + len(value) != fixed or not _DIGITS.match(value):
            problems.append("format")
    elif len(value) > _MAX_VARIABLE.get(ai, _MAX_VARIABLE.get(ai[:2], 30)):
        problems.append("too_long")
    if not _VALUE.match(value):
        problems.append("format")
    if ai in ("00", "01", "02") and not problems and not check_digit_holds(value):
        problems.append("check_digit")
    if ai[:2] in ("11", "12", "13", "15", "16", "17") and not problems and _date(value) is None:
        problems.append("date")
    return problems


def _scan(fields: list[tuple[str, str]], problems: list[str]) -> Gs1Scan:
    elements: dict[str, str] = {}
    for ai, value in fields:
        problems.extend(_field_problems(ai, value))
        elements.setdefault(ai, value)
    return Gs1Scan(elements, problems=tuple(dict.fromkeys(problems)))


def _date(value: str | None, *, today: date | None = None) -> date | None:
    """A GS1 YYMMDD date: the century nearest today, a day of 00 the month's last."""
    if value is None or len(value) != 6 or not _DIGITS.match(value):
        return None
    yy, month, day = int(value[:2]), int(value[2:4]), int(value[4:])
    now = (today or date.today()).year
    century = now - now % 100
    year = century + yy
    if year - now > 50:
        year -= 100
    elif now - year > 49:
        year += 100
    if not 1 <= month <= 12:
        return None
    if day == 0:
        day = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, day)
    except ValueError:
        return None
