"""Rules that do not need a drawing: money, code edition, and code claims."""

from datetime import date
from decimal import Decimal

import pytest

from adel_takeoff.code_notes import (
    analyze_drawing_text,
    edition_for,
    format_code_statement,
)
from adel_takeoff.costing import MarkupRejected, parse_markup
from adel_takeoff.constants import PERMIT_CODE_CUTOFF


def test_markup_blank_accepted_and_over_five_rejected():
    assert parse_markup(None) is None
    assert parse_markup("") is None
    assert parse_markup("   ") is None
    assert parse_markup("0.35") == Decimal("0.35")
    assert parse_markup("5") == Decimal("5")
    with pytest.raises(MarkupRejected, match="greater than 5"):
        parse_markup("5.01")
    with pytest.raises(MarkupRejected, match="greater than 5"):
        parse_markup("6")


def test_code_edition_flips_after_cutoff():
    assert PERMIT_CODE_CUTOFF == date(2026, 12, 31)
    on_cutoff = edition_for(date(2026, 12, 31))
    assert on_cutoff.fbc == "FBC 8th Edition"
    assert on_cutoff.nec == "NEC 2020"
    assert on_cutoff.basis == "permit-issue date"
    after = edition_for(date(2027, 1, 1))
    assert after.fbc == "FBC 9th Edition"
    assert after.nec == "NEC 2023"
    missing = edition_for(None)
    assert missing.fbc == "unknown"
    assert missing.nec == "unknown"
    assert missing.basis == "unknown"


def test_code_claim_without_a_section_is_unknown():
    rendered = format_code_statement("All work shall comply with code.", None)
    assert "unknown" in rendered
    assert "section:" in rendered
    cited = format_code_statement("Supports shall comply.", "NEC 300.11")
    assert "NEC 300.11" in cited
    assert "unknown" not in cited


def test_nec_2023_drawing_text_is_an_rfi_not_the_edition():
    rfis, claims = analyze_drawing_text("DRAWING NOTE: NEC 2023")
    assert any(rfi.topic.startswith("Drawing text says NEC 2023") for rfi in rfis)
    assert all(rfi.disposition == "rfi" for rfi in rfis)
    assert edition_for(date(2026, 6, 1)).nec == "NEC 2020"


def test_mixed_raceway_is_an_rfi_with_sections_and_not_a_violation():
    text = "NOTE: POWER AND CLASS 2 COMMUNICATIONS IN ONE RACEWAY."
    rfis, _claims = analyze_drawing_text(text)
    mixed = [rfi for rfi in rfis if "725.136" in rfi.detail and "800.133" in rfi.detail]
    assert len(mixed) == 1
    assert mixed[0].disposition == "rfi"
    assert "violation" not in mixed[0].detail.lower()
    separated, _claims = analyze_drawing_text(
        "Power is in a separate raceway from Class 2 communications."
    )
    assert all("725.136" not in rfi.detail for rfi in separated)


def test_requirement_without_a_section_is_marked_unknown():
    _rfis, claims = analyze_drawing_text("ALL WORK SHALL COMPLY WITH CODE.")
    assert len(claims) == 1
    assert claims[0].section == "unknown"
    assert "unknown" in claims[0].rendered
