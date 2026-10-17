"""Synthetic electrical sheets. Geometry is drawn, not traced from a real set."""

from __future__ import annotations

import fitz

# Shared overlap frame. Both sheets use the same along/into placement.
MATCH_TOP = 120
MATCH_BOTTOM = 700
OVERLAP_Y = 300
OVERLAP_DEPTH = 120
E1_MATCH_X = 700
E2_MATCH_X = 72
OVERLAP_INTO = 40


def _text(page, x, y, text, size=9):
    page.insert_text(fitz.Point(x, y), text, fontsize=size, fontname="helv")


def draw_duplex(page, x, y):
    page.draw_circle(fitz.Point(x, y), 8, color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 4, y - 2), fitz.Point(x + 4, y - 2), color=(0, 0, 0), width=0.6)
    page.draw_line(fitz.Point(x - 4, y + 2), fitz.Point(x + 4, y + 2), color=(0, 0, 0), width=0.6)


def draw_recessed(page, x, y):
    page.draw_circle(fitz.Point(x, y), 10, color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 7, y), fitz.Point(x + 7, y), color=(0, 0, 0), width=0.6)
    page.draw_line(fitz.Point(x, y - 7), fitz.Point(x, y + 7), color=(0, 0, 0), width=0.6)


def draw_switch(page, x, y):
    page.draw_rect(fitz.Rect(x - 8, y - 8, x + 8, y + 8), color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 6, y + 6), fitz.Point(x + 6, y - 6), color=(0, 0, 0), width=0.6)


def draw_panelboard(page, x, y):
    page.draw_rect(fitz.Rect(x - 7, y - 11, x + 7, y + 11), color=(0, 0, 0), width=0.9)


def draw_landscape(page, x, y):
    page.draw_circle(fitz.Point(x, y), 9, color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x, y + 9), fitz.Point(x, y + 16), color=(0, 0, 0), width=0.8)


def draw_control(page, x, y):
    page.draw_rect(fitz.Rect(x - 9, y - 5, x + 9, y + 5), color=(0, 0, 0), width=0.8)
    page.draw_circle(fitz.Point(x, y), 3, color=(0, 0, 0), width=0.6)


def draw_unknown(page, x, y):
    page.draw_line(fitz.Point(x - 8, y + 6), fitz.Point(x + 8, y + 6), color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x + 8, y + 6), fitz.Point(x, y - 8), color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x, y - 8), fitz.Point(x - 8, y + 6), color=(0, 0, 0), width=0.8)


def _legend_row(page, draw, x, y, label):
    draw(page, x, y)
    _text(page, x + 28, y + 3, label)


def _match_line(page, x):
    page.draw_line(
        fitz.Point(x, MATCH_TOP),
        fitz.Point(x, MATCH_BOTTOM),
        color=(0, 0, 0),
        width=1,
    )
    _text(page, x + 8, MATCH_TOP + 24, f"MATCH LINE MLA OVERLAP {OVERLAP_DEPTH}")


def build_sample_pdf() -> bytes:
    document = fitz.open()
    legend = document.new_page(width=612, height=792)
    _text(legend, 72, 48, "SYMBOL LEGEND", size=12)
    symbol_x = 90
    _legend_row(legend, draw_duplex, symbol_x, 100, "DUPLEX RECEPTACLE")
    _legend_row(legend, draw_recessed, symbol_x, 150, "RECESSED LIGHT")
    _legend_row(legend, draw_switch, symbol_x, 200, "SINGLE POLE SWITCH")
    _legend_row(legend, draw_panelboard, symbol_x, 250, "PANELBOARD")
    _legend_row(legend, draw_landscape, symbol_x, 300, "LANDSCAPE LIGHT")
    _legend_row(legend, draw_control, symbol_x, 350, "LIGHTING CONTROL")

    sheet_e1 = document.new_page(width=900, height=792)
    _text(sheet_e1, 72, 36, "SHEET E1", size=12)
    _text(sheet_e1, 220, 36, "TITLE FIRST FLOOR POWER", size=12)
    draw_duplex(sheet_e1, 150, 160)
    _text(sheet_e1, 176, 163, "LP1-12")
    _text(sheet_e1, 176, 145, "GRID B-4")
    draw_duplex(sheet_e1, 150, 250)
    _text(sheet_e1, 176, 253, "SEE NOTE")
    draw_recessed(sheet_e1, 150, 340)
    _text(sheet_e1, 176, 343, "PANEL LP2 CIRCUIT 4")
    draw_switch(sheet_e1, 150, 430)
    draw_unknown(sheet_e1, 150, 520)
    draw_landscape(sheet_e1, 320, 160)
    draw_control(sheet_e1, 320, 250)
    draw_panelboard(sheet_e1, 320, 520)
    # Overlap copy. Symmetric symbol, so the bbox center is the insertion point.
    draw_duplex(sheet_e1, E1_MATCH_X - OVERLAP_INTO, OVERLAP_Y)
    _text(sheet_e1, E1_MATCH_X - OVERLAP_INTO - 8, OVERLAP_Y - 28, "LP1-7")
    _match_line(sheet_e1, E1_MATCH_X)
    _text(sheet_e1, 72, 640, "DRAWING NOTE: NEC 2023")
    _text(sheet_e1, 72, 656, "ALL WORK SHALL COMPLY WITH CODE.")
    _text(sheet_e1, 72, 672, "SUPPORTS SHALL COMPLY WITH NEC 300.11.")
    _text(sheet_e1, 72, 688, "NOTE: POWER AND CLASS 2 COMMUNICATIONS IN ONE RACEWAY.")

    sheet_e2 = document.new_page(width=612, height=792)
    _text(sheet_e2, 72, 36, "SHEET E2", size=12)
    _text(sheet_e2, 220, 36, "TITLE FIRST FLOOR LIGHTING", size=12)
    _match_line(sheet_e2, E2_MATCH_X)
    draw_duplex(sheet_e2, E2_MATCH_X + OVERLAP_INTO, OVERLAP_Y)
    draw_recessed(sheet_e2, 300, 200)
    _text(sheet_e2, 326, 203, "HP1/3")

    payload = document.tobytes()
    document.close()
    return payload


def build_scheduled_pdf() -> bytes:
    """Sample sheets plus type tags, a fixture schedule, and panel schedules.

    The sample geometry is drawn again here so build_sample_pdf stays unchanged.
    Schedule words are on their own sheet, away from the symbols.
    """

    document = fitz.open()
    legend = document.new_page(width=612, height=792)
    _text(legend, 72, 48, "SYMBOL LEGEND", size=12)
    symbol_x = 90
    _legend_row(legend, draw_duplex, symbol_x, 100, "DUPLEX RECEPTACLE")
    _legend_row(legend, draw_recessed, symbol_x, 150, "RECESSED LIGHT")
    _legend_row(legend, draw_switch, symbol_x, 200, "SINGLE POLE SWITCH")
    _legend_row(legend, draw_panelboard, symbol_x, 250, "PANELBOARD")
    _legend_row(legend, draw_landscape, symbol_x, 300, "LANDSCAPE LIGHT")
    _legend_row(legend, draw_control, symbol_x, 350, "LIGHTING CONTROL")

    sheet_e1 = document.new_page(width=900, height=792)
    _text(sheet_e1, 72, 36, "SHEET E1", size=12)
    _text(sheet_e1, 220, 36, "TITLE FIRST FLOOR POWER", size=12)
    draw_duplex(sheet_e1, 150, 160)
    _text(sheet_e1, 176, 163, "LP1-12")
    _text(sheet_e1, 176, 145, "GRID B-4")
    draw_duplex(sheet_e1, 150, 250)
    _text(sheet_e1, 176, 253, "SEE NOTE")
    draw_recessed(sheet_e1, 150, 340)
    _text(sheet_e1, 176, 320, "TYPE A")
    _text(sheet_e1, 176, 343, "PANEL LP2 CIRCUIT 4")
    draw_recessed(sheet_e1, 450, 200)
    _text(sheet_e1, 476, 184, "TYPE B")
    _text(sheet_e1, 476, 218, '6\'-0" AFF')
    draw_recessed(sheet_e1, 450, 400)
    _text(sheet_e1, 476, 403, "TYPE Z")
    draw_switch(sheet_e1, 150, 430)
    _text(sheet_e1, 176, 433, "LP9-1")
    draw_unknown(sheet_e1, 150, 520)
    draw_landscape(sheet_e1, 320, 160)
    draw_control(sheet_e1, 320, 250)
    draw_panelboard(sheet_e1, 320, 520)
    draw_duplex(sheet_e1, E1_MATCH_X - OVERLAP_INTO, OVERLAP_Y)
    _text(sheet_e1, E1_MATCH_X - OVERLAP_INTO - 8, OVERLAP_Y - 28, "LP1-7")
    _match_line(sheet_e1, E1_MATCH_X)
    _text(sheet_e1, 72, 640, "DRAWING NOTE: NEC 2023")
    _text(sheet_e1, 72, 656, "ALL WORK SHALL COMPLY WITH CODE.")
    _text(sheet_e1, 72, 672, "SUPPORTS SHALL COMPLY WITH NEC 300.11.")
    _text(sheet_e1, 72, 688, "NOTE: POWER AND CLASS 2 COMMUNICATIONS IN ONE RACEWAY.")

    sheet_e2 = document.new_page(width=612, height=792)
    _text(sheet_e2, 72, 36, "SHEET E2", size=12)
    _text(sheet_e2, 220, 36, "TITLE FIRST FLOOR LIGHTING", size=12)
    _match_line(sheet_e2, E2_MATCH_X)
    draw_duplex(sheet_e2, E2_MATCH_X + OVERLAP_INTO, OVERLAP_Y)
    draw_recessed(sheet_e2, 300, 200)
    _text(sheet_e2, 326, 188, "TYPE A")
    _text(sheet_e2, 326, 203, "HP1/3")
    _text(sheet_e2, 326, 248, '7\'-0" AFF')

    _schedule_page(document)

    payload = document.tobytes()
    document.close()
    return payload


def _schedule_page(document) -> None:
    page = document.new_page(width=1100, height=792)
    _text(page, 72, 36, "SHEET E3", size=12)
    _text(page, 220, 36, "TITLE FIXTURE AND PANEL SCHEDULES", size=12)

    _text(page, 72, 80, "LIGHTING FIXTURE SCHEDULE", size=12)
    fixture_headers = (
        (72, "TYPE"),
        (160, "DESCRIPTION"),
        (420, "MANUFACTURER"),
        (560, "LAMP"),
        (640, "VOLTAGE"),
        (740, "MOUNTING"),
        (880, "HEIGHT"),
        (1000, "COUNT"),
    )
    for x, label in fixture_headers:
        _text(page, x, 108, label)
    fixture_rows = (
        ("A", "6 INCH DOWNLIGHT", "ACME", "LED", "120", "RECESSED", '9\'-0"', "3"),
        ("B", "WALL SCONCE", "ACME", "LED", "120", "WALL", '6\'-0"', "1"),
        ("C", "PENDANT", "ACME", "LED", "277", "PENDANT", '10\'-0"', "1"),
    )
    for offset, cells in enumerate(fixture_rows):
        ys = 128 + offset * 20
        for x, text in zip((item[0] for item in fixture_headers), cells):
            _text(page, x, ys, text)

    _panel_block(
        page,
        250,
        "PANEL SCHEDULE LP1",
        (
            ("12", "DUPLEX RECEPTACLE", "20A", "1", "180"),
            ("15", "SPARE", "20A", "1", "0"),
        ),
    )
    _panel_block(
        page,
        400,
        "PANEL SCHEDULE LP2",
        (("4", "RECESSED FIXTURE", "20A", "1", "100"),),
    )
    _panel_block(
        page,
        520,
        "PANEL SCHEDULE HP1",
        (("3", "RECESSED FIXTURE", "20A", "1", "80"),),
    )


def _panel_block(page, top: float, title: str, rows: tuple[tuple[str, ...], ...]) -> None:
    _text(page, 72, top, title, size=12)
    headers = (
        (72, "CIRCUIT"),
        (180, "DESCRIPTION"),
        (460, "BREAKER"),
        (580, "POLES"),
        (680, "LOAD"),
    )
    for x, label in headers:
        _text(page, x, top + 28, label)
    xs = [item[0] for item in headers]
    for offset, cells in enumerate(rows):
        for x, text in zip(xs, cells):
            _text(page, x, top + 48 + offset * 20, text)
