"""Reading a scan: GS1 labels in every form scanners and cameras hand them
over, retail barcodes, and plain codes."""

from __future__ import annotations

from datetime import date

import pytest

from seneschal_core.gs1 import (
    GS,
    Gs1Scan,
    PlainScan,
    _date,
    check_digit_holds,
    normalize_gtin,
    read_scan,
)

#: GS1's own example GTIN and SSCC (General Specifications).
GTIN = "09506000134352"
SSCC = "106141411234567897"


def gs1(text: str, **kwargs: bool) -> Gs1Scan:
    scan = read_scan(text, **kwargs)
    assert isinstance(scan, Gs1Scan), scan
    return scan


@pytest.mark.parametrize(
    "text",
    [
        f"(01){GTIN}(10)L24-118(17)270630",
        f"01{GTIN}10L24-118{GS}17270630",
        f"01{GTIN}17270630{GS}10L24-118",
        f"01{GTIN}1727063010L24-118",
        f"]C101{GTIN}10L24-118{GS}17270630",
        f"]d201{GTIN}10L24-118{GS}17270630",
        f"]Q301{GTIN}17270630{GS}10L24-118",
        f"{GS}01{GTIN}1727063010L24-118",
        f"https://id.gs1.org/01/{GTIN}/10/L24-118?17=270630",
        f"https://example.com/products/gtin/{GTIN}/lot/L24-118?exp=270630&linkType=all",
    ],
)
def test_each_form_a_gs1_label_comes_in_reads_the_same(text):
    scan = gs1(text)

    assert (scan.gtin, scan.lot, scan.expires_on) == (GTIN, "L24-118", date(2027, 6, 30))
    assert scan.problems == ()


def test_a_label_names_its_serial_production_date_best_before_and_count():
    scan = gs1(f"(01){GTIN}(11)260115(15)261231(21)SN-0042(30)12")

    assert (scan.produced_on, scan.best_before, scan.serial, scan.count) == (
        date(2026, 1, 15),
        date(2026, 12, 31),
        "SN-0042",
        12,
    )


def test_a_pallet_label_names_its_sscc():
    assert gs1(f"00{SSCC}").sscc == SSCC


@pytest.mark.parametrize(
    ("barcode", "gtin"),
    [
        ("96385074", "00000096385074"),  # EAN-8
        ("012345678905", "00012345678905"),  # UPC-A
        ("4006381333931", "04006381333931"),  # EAN-13
        (GTIN, GTIN),  # ITF-14
        ("]E04006381333931", "04006381333931"),
    ],
)
def test_a_retail_barcode_is_a_gtin(barcode, gtin):
    scan = gs1(barcode)

    assert scan.gtin == gtin
    assert scan.raw == barcode.removeprefix("]E0")


@pytest.mark.parametrize("text", ["SKU-1234", "R-0012", "P-0003-17", "]C0R-0012", "12345"])
def test_an_apps_own_code_is_plain(text):
    assert read_scan(text) == PlainScan(text.removeprefix("]C0"))


def test_a_scanner_that_says_its_symbol_is_plain_is_believed():
    assert read_scan("4006381333931", gs1=False) == PlainScan("4006381333931")


def test_an_own_sku_that_reads_as_a_gtin_keeps_what_was_scanned():
    # An 8-digit SKU with a valid check digit: the app can still find it by SKU.
    assert gs1("96385074").raw == "96385074"


def test_a_lot_that_swallowed_the_fields_after_it_is_ambiguous():
    # The scanner dropped the GS after the lot, so the expiry reads as lot.
    scan = gs1(f"01{GTIN}10L24-11817270630")

    assert scan.lot == "L24-11817270630"
    assert scan.ambiguous


def test_a_lot_at_the_end_is_not_ambiguous():
    assert not gs1(f"01{GTIN}17270630{GS}10L24-118").ambiguous


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("(01)09506000134359(10)A", "check_digit"),
        ("(00)106141411234567890", "check_digit"),
        (f"(01){GTIN}(17)271340", "date"),
        (f"(01){GTIN}(17)270631", "date"),
        (f"(01){GTIN}(10){'X' * 21}", "too_long"),
        (f"(01){GTIN}(17)2706", "format"),
        (f"(01){GTIN}(10)café", "format"),
    ],
)
def test_a_label_read_with_something_wrong_says_what(text, problem):
    assert problem in gs1(text).problems


def test_the_first_value_of_an_ai_stands():
    assert gs1(f"(01){GTIN}(10)FIRST(10)SECOND").lot == "FIRST"


def test_a_day_of_zero_is_the_months_last_day():
    assert _date("270200") == date(2027, 2, 28)
    assert _date("280200") == date(2028, 2, 29)


def test_a_year_is_in_the_century_nearest_today():
    today = date(2026, 11, 18)

    assert _date("761231", today=today) == date(2076, 12, 31)
    assert _date("770101", today=today) == date(1977, 1, 1)
    assert _date("000101", today=today) == date(2000, 1, 1)


def test_a_gtin_is_normalised_to_fourteen_digits_only_when_its_check_digit_holds():
    assert normalize_gtin(" 012345678905 ") == "00012345678905"
    assert normalize_gtin("012345678900") is None
    assert normalize_gtin("12345") is None
    assert normalize_gtin("0123456789AB") is None


def test_the_check_digit_is_gs1s():
    assert check_digit_holds(GTIN)
    assert check_digit_holds(SSCC)
    assert not check_digit_holds("09506000134359")


def test_an_empty_scan_is_plain_and_empty():
    assert read_scan(" \r\n") == PlainScan("")


def test_a_link_without_an_identifier_is_plain():
    url = "https://example.com/about/10/x"

    assert read_scan(url) == PlainScan(url)
