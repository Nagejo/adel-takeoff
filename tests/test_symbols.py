"""Example search, the company symbol library, and unlegended marks."""

import math

import fitz
from fastapi.testclient import TestClient

from adel_takeoff.constants import CONFIDENCE_HIGH_MIN
from adel_takeoff.main import create_app
from adel_takeoff.symbols import (
    REVIEW_NOTE,
    review_status_for,
    similarity,
    starter_symbols,
)
from adel_takeoff.takeoff import run_takeoff
from tests.drawings import build_sample_pdf, draw_duplex, draw_switch, draw_unknown


def _turn(page, x, y, degrees, draw):
    if degrees % 360 == 0:
        draw(page, x, y)
        return
    radians = math.radians(degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)

    def rot(px, py):
        dx = px - x
        dy = py - y
        return fitz.Point(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)

    if draw is draw_switch:
        corners = [(x - 8, y - 8), (x + 8, y - 8), (x + 8, y + 8), (x - 8, y + 8)]
        points = [rot(*point) for point in corners]
        for start, end in zip(points, points[1:] + points[:1]):
            page.draw_line(start, end, color=(0, 0, 0), width=0.8)
        page.draw_line(rot(x - 6, y + 6), rot(x + 6, y - 6), color=(0, 0, 0), width=0.6)
        return
    page.draw_circle(fitz.Point(x, y), 8, color=(0, 0, 0), width=0.8)
    page.draw_line(rot(x - 4, y - 2), rot(x + 4, y - 2), color=(0, 0, 0), width=0.6)
    page.draw_line(rot(x - 4, y + 2), rot(x + 4, y + 2), color=(0, 0, 0), width=0.6)


def _mirrored_switch(page, x, y):
    page.draw_rect(fitz.Rect(x - 8, y - 8, x + 8, y + 8), color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x + 6, y + 6), fitz.Point(x - 6, y - 6), color=(0, 0, 0), width=0.6)


def _capture(draw) -> list:
    document = fitz.open()
    page = document.new_page(width=160, height=160)
    page.insert_text(fitz.Point(20, 20), "SHEET T1")
    draw(page)
    payload = document.tobytes()
    document.close()
    from adel_takeoff.extract import read_pdf

    return read_pdf(payload)[0].clusters[0].primitives


def _symbol(symbol_id):
    return next(symbol for symbol in starter_symbols() if symbol.id == symbol_id)


def test_rotation_and_mirror_match_the_same_symbol():
    switch = _symbol("starter-switch")
    duplex = _symbol("starter-duplex")
    rotated = _capture(lambda page: _turn(page, 80, 80, 90, draw_switch))
    score, _transform = similarity(switch.primitives, rotated)
    assert score >= CONFIDENCE_HIGH_MIN

    finer = _capture(lambda page: _turn(page, 80, 80, 45, draw_switch))
    finer_score, finer_transform = similarity(switch.primitives, finer)
    assert finer_score >= CONFIDENCE_HIGH_MIN
    assert any(step in finer_transform for step in ("45", "135", "225", "315"))

    mirrored = _capture(lambda page: _mirrored_switch(page, 80, 80))
    mirror_score, mirror_transform = similarity(switch.primitives, mirrored)
    assert mirror_score >= CONFIDENCE_HIGH_MIN
    assert "mirror" in mirror_transform

    other, _name = similarity(switch.primitives, duplex.primitives)
    assert other < 0.5


def test_cutoff_holds_a_weaker_match_for_review():
    duplex = _symbol("starter-duplex")
    tilted = _capture(lambda page: _turn(page, 80, 80, 45, draw_duplex))
    score, _transform = similarity(duplex.primitives, tilted)
    assert 0.6 <= score < CONFIDENCE_HIGH_MIN
    assert review_status_for(score) == "pending"
    assert review_status_for(0.95) == "accepted"
    assert review_status_for(CONFIDENCE_HIGH_MIN) == "accepted"


def _plan() -> bytes:
    document = fitz.open()
    legend = document.new_page(width=612, height=792)
    legend.insert_text(fitz.Point(72, 48), "SYMBOL LEGEND", fontsize=12)
    draw_switch(legend, 90, 110)
    legend.insert_text(fitz.Point(130, 113), "SINGLE POLE SWITCH")
    draw_duplex(legend, 90, 170)
    legend.insert_text(fitz.Point(130, 173), "DUPLEX RECEPTACLE")

    plan = document.new_page(width=612, height=792)
    plan.insert_text(fitz.Point(72, 36), "SHEET E1", fontsize=12)
    plan.insert_text(fitz.Point(220, 36), "TITLE FIRST FLOOR POWER", fontsize=12)
    _turn(plan, 180, 180, 90, draw_switch)
    _turn(plan, 180, 300, 45, draw_duplex)
    draw_unknown(plan, 400, 180)
    payload = document.tobytes()
    document.close()
    return payload


def _upload(client: TestClient, payload: bytes, name: str) -> str:
    response = client.post(
        "/jobs",
        files={"pdf": (name, payload, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rstrip("/").split("/")[-1]


def test_unlegended_symbol_can_be_an_rfi():
    result = run_takeoff(build_sample_pdf())
    unmatched = [item for item in result.items if item.method == "unmatched"]
    assert len(unmatched) == 1
    flagged = [rfi for rfi in result.rfis if rfi.topic == "Unlegended symbol"]
    assert len(flagged) == 1
    assert flagged[0].disposition == "rfi"
    assert "missing from the legend" in flagged[0].detail
    assert "violation" not in flagged[0].detail.lower()


def test_cutoff_review_and_library_reuse(tmp_path):
    client = TestClient(create_app(tmp_path))
    job_id = _upload(client, _plan(), "plan.pdf")
    review = client.get(f"/jobs/{job_id}/review").text
    assert "Unlegended symbol" in review
    assert 'data-disposition="rfi"' in review

    held = client.post(
        f"/jobs/{job_id}/symbols/search",
        data={"symbol_id": "starter-duplex", "cutoff": "0.95"},
        follow_redirects=True,
    )
    assert held.status_code == 200
    assert 'data-review="pending"' not in held.text

    found = client.post(
        f"/jobs/{job_id}/symbols/search",
        data={"symbol_id": "starter-duplex", "cutoff": "0.60"},
        follow_redirects=True,
    )
    assert found.status_code == 200
    assert 'data-review="pending"' in found.text
    assert "check" in found.text

    turned = client.post(
        f"/jobs/{job_id}/symbols/search",
        data={"symbol_id": "starter-switch", "cutoff": "0.75"},
        follow_redirects=True,
    )
    assert 'data-review="accepted"' in turned.text
    page = client.get(f"/jobs/{job_id}/review").text
    assert "Needs review" in page
    assert REVIEW_NOTE in page
    assert 'id="power-device-count">1' in page
    assert "violation" not in page.lower()

    added = client.post(
        f"/jobs/{job_id}/symbols",
        data={
            "name": "Site mark",
            "category": "other",
            "tag": "SITE MARK",
            "assembly_id": "",
            "page_index": "1",
            "x0": "380",
            "y0": "160",
            "x1": "420",
            "y1": "200",
        },
        follow_redirects=True,
    )
    assert "Site mark" in added.text
    assert 'data-origin="company"' in added.text
    assert 'data-symbol="site-mark"' in added.text

    other = fitz.open()
    sheet = other.new_page(width=400, height=300)
    sheet.insert_text(fitz.Point(40, 30), "SHEET E4")
    draw_unknown(sheet, 120, 140)
    payload = other.tobytes()
    other.close()
    second = _upload(client, payload, "other.pdf")
    library = client.get(f"/jobs/{second}/symbols").text
    assert "Site mark" in library
    assert 'data-symbol="starter-duplex"' in library
    reused = client.post(
        f"/jobs/{second}/symbols/search",
        data={"symbol_id": "site-mark", "cutoff": "0.75"},
        follow_redirects=True,
    )
    assert reused.status_code == 200
    assert "SITE MARK" in reused.text
    assert 'data-review="accepted"' in reused.text
