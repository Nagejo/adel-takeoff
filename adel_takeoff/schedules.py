"""Read fixture and panel schedules from drawing text.

A cell is copied only when a header column claims it. A count is an integer
only when the cell is a whole number. Anything else stays the printed text.
Missing cells stay blank. This module does not invent a value, and it does
not look at the symbol legend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from adel_takeoff.constants import (
    SCHEDULE_HEADER_PHRASE_GAP_PT,
    SCHEDULE_HEADER_WINDOW_PT,
    SCHEDULE_ROW_GAP_PT,
)
from adel_takeoff.extract import (
    OCR_VALUE_NOTE,
    PageData,
    Word,
    group_lines,
    is_low_ocr,
    min_ocr_confidence,
)


_MARK = re.compile(r"[A-Z]{1,4}\d{0,3}")
_WHOLE_NUMBER = re.compile(r"\d+")
_PANEL_HEADING = re.compile(r"\b(?:PANELBOARD|PANEL)\s+SCHEDULE\b")
_PANEL_NAME = re.compile(r"\b(?:PANELBOARD|PANEL)\s+SCHEDULE\s+([A-Z]{1,6}\d{1,4})\b")

# Longest phrase first. A multi-word phrase matches only when its words sit
# in one column title. MOUNTING and HEIGHT in separate columns stay separate.
_FIXTURE_ALIASES = (
    ("MOUNTING HEIGHT", "mounting_height"),
    ("TYPE/MARK", "mark"),
    ("MARK/TYPE", "mark"),
    ("TYPE MARK", "mark"),
    ("FIXTURE TYPE", "mark"),
    ("DESCRIPTION", "description"),
    ("MANUFACTURER", "manufacturer"),
    ("VOLTAGE", "voltage"),
    ("MOUNTING", "mounting"),
    ("QUANTITY", "count"),
    ("HEIGHT", "mounting_height"),
    ("COUNT", "count"),
    ("LAMP", "lamp"),
    ("TYPE", "mark"),
    ("MARK", "mark"),
    ("QTY", "count"),
)
_PANEL_ALIASES = (
    ("DESCRIPTION", "description"),
    ("BREAKER", "breaker"),
    ("CIRCUIT", "circuit"),
    ("POLES", "poles"),
    ("POLE", "poles"),
    ("LOAD", "load"),
    ("CKT", "circuit"),
)
_FIXTURE_SUPPORT = {
    "description",
    "manufacturer",
    "lamp",
    "voltage",
    "mounting",
    "mounting_height",
    "count",
}
_PANEL_SUPPORT = {"description", "breaker", "poles", "load"}
_FIXTURE_TITLES = (
    "LIGHTING FIXTURE SCHEDULE",
    "LUMINAIRE SCHEDULE",
    "FIXTURE SCHEDULE",
)


@dataclass(frozen=True)
class FixtureScheduleRow:
    mark: str
    description: str
    manufacturer: str
    lamp: str
    voltage: str
    mounting: str
    mounting_height: str
    count: int | None
    count_text: str
    page_index: int
    sheet: str
    source: str = "pdf"
    ocr_check: bool = False

    def as_dict(self, *, used: bool) -> dict:
        return {
            "mark": self.mark,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "lamp": self.lamp,
            "voltage": self.voltage,
            "mounting": self.mounting,
            "mounting_height": self.mounting_height,
            "count": self.count,
            "count_text": self.count_text,
            "page_index": self.page_index,
            "sheet": self.sheet,
            "used": used,
            "source": self.source,
            "ocr_check": self.ocr_check,
        }


@dataclass(frozen=True)
class PanelScheduleRow:
    panel: str
    circuit: str
    description: str
    breaker: str
    poles: str
    load: str
    page_index: int
    sheet: str
    source: str = "pdf"
    ocr_check: bool = False

    def as_dict(self, *, used: bool) -> dict:
        return {
            "panel": self.panel,
            "circuit": self.circuit,
            "description": self.description,
            "breaker": self.breaker,
            "poles": self.poles,
            "load": self.load,
            "page_index": self.page_index,
            "sheet": self.sheet,
            "used": used,
            "source": self.source,
            "ocr_check": self.ocr_check,
        }


@dataclass
class ParsedSchedules:
    fixture_rows: list[FixtureScheduleRow] = field(default_factory=list)
    fixture_by_mark: dict[str, FixtureScheduleRow] = field(default_factory=dict)
    duplicate_marks: list[str] = field(default_factory=list)
    panel_rows: list[PanelScheduleRow] = field(default_factory=list)
    panel_by_key: dict[tuple[str, str], PanelScheduleRow] = field(default_factory=dict)
    duplicate_circuits: list[tuple[str, str]] = field(default_factory=list)
    panel_names: list[str] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    fixture_table_count: int = 0
    panel_table_count: int = 0
    ocr_checks: list[dict] = field(default_factory=list)


def normalize_token(text: str) -> str:
    return re.sub(r"[^A-Z0-9/]", "", text.upper())


def whole_number(text: str) -> int | None:
    """An integer only when the entire cell is digits. '2 EA' is not 2."""

    stripped = text.strip()
    if _WHOLE_NUMBER.fullmatch(stripped):
        return int(stripped)
    return None


def normalize_circuit(text: str) -> str:
    """Compare circuit numbers numerically so 012 and 12 are the same circuit."""

    stripped = text.strip()
    if not stripped:
        return ""
    if _WHOLE_NUMBER.fullmatch(stripped):
        return str(int(stripped))
    first = stripped.split()[0]
    if _WHOLE_NUMBER.fullmatch(first):
        return str(int(first))
    return first.upper()


def _line_text(words: list[Word]) -> str:
    return " ".join(word.text for word in words)


def _upper_line(words: list[Word]) -> str:
    return " ".join(_line_text(words).upper().split())


def _fixture_heading(words: list[Word]) -> bool:
    text = _upper_line(words)
    return any(title in text for title in _FIXTURE_TITLES)


def _panel_heading(words: list[Word]) -> str | None:
    """None when the line is not a panel schedule. Blank when the name is missing."""

    text = _upper_line(words)
    if _PANEL_HEADING.search(text) is None:
        return None
    match = _PANEL_NAME.search(text)
    if match is None:
        return ""
    return match.group(1)


def _match_headers(words: list[Word], aliases: tuple[tuple[str, str], ...]) -> list[tuple[str, float]]:
    ordered = sorted(aliases, key=lambda item: (-len(item[0].split()), -len(item[0])))
    found: list[tuple[str, float]] = []
    seen: set[str] = set()
    index = 0
    while index < len(words):
        hit: tuple[str, list[Word]] | None = None
        for phrase, field in ordered:
            parts = phrase.split()
            chunk = words[index : index + len(parts)]
            if len(chunk) != len(parts):
                continue
            if [normalize_token(word.text) for word in chunk] != parts:
                continue
            if any(
                right.x0 - left.x1 > SCHEDULE_HEADER_PHRASE_GAP_PT
                for left, right in zip(chunk, chunk[1:])
            ):
                continue
            hit = (field, chunk)
            break
        if hit is None:
            index += 1
            continue
        field, chunk = hit
        if field not in seen:
            center = (chunk[0].x0 + chunk[-1].x1) / 2
            found.append((field, center))
            seen.add(field)
        index += len(chunk)
    return found


def _fixture_header(anchors: list[tuple[str, float]]) -> bool:
    fields = {field for field, _center in anchors}
    if "mark" not in fields:
        return False
    return len(fields & _FIXTURE_SUPPORT) >= 2


def _panel_header(anchors: list[tuple[str, float]]) -> bool:
    fields = {field for field, _center in anchors}
    if "circuit" not in fields:
        return False
    return len(fields & _PANEL_SUPPORT) >= 1


def _spans(anchors: list[tuple[str, float]]) -> list[tuple[str, float, float]]:
    ordered = sorted(anchors, key=lambda item: item[1])
    spans = []
    for index, (field, center) in enumerate(ordered):
        if index == 0:
            left = float("-inf")
        else:
            left = (ordered[index - 1][1] + center) / 2
        if index == len(ordered) - 1:
            right = float("inf")
        else:
            right = (center + ordered[index + 1][1]) / 2
        spans.append((field, left, right))
    return spans


def _line_low(words: list[Word]) -> bool:
    return any(is_low_ocr(word) for word in words)


def _word_box(words: list[Word]) -> tuple[float, float, float, float]:
    return (
        min(word.x0 for word in words),
        min(word.y0 for word in words),
        max(word.x1 for word in words),
        max(word.y1 for word in words),
    )


def _ocr_check(kind: str, page: PageData, words: list[Word], text: str) -> dict:
    x0, y0, x1, y1 = _word_box(words)
    return {
        "kind": kind,
        "sheet": page.sheet,
        "page_index": page.index,
        "text": text,
        "confidence": min_ocr_confidence(words),
        "note": OCR_VALUE_NOTE,
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
    }


def _row_source(page: PageData, words: list[Word]) -> str:
    if page.text_source == "ocr" or any(word.source == "ocr" for word in words):
        return "ocr"
    return "pdf"


def _cells(
    words: list[Word], spans: list[tuple[str, float, float]]
) -> tuple[dict[str, str], set[str]]:
    buckets: dict[str, list[str]] = {field: [] for field, _left, _right in spans}
    withheld: set[str] = set()
    for word in sorted(words, key=lambda item: item.x0):
        for field, left, right in spans:
            if left <= word.cx < right:
                if is_low_ocr(word):
                    withheld.add(field)
                else:
                    buckets[field].append(word.text)
                break
    return {field: " ".join(parts).strip() for field, parts in buckets.items()}, withheld


def _mark_token(text: str) -> str:
    for token in text.split():
        cleaned = normalize_token(token)
        if _MARK.fullmatch(cleaned):
            return cleaned
    return ""


def _bbox(lines: list[list[Word]]) -> tuple[float, float, float, float]:
    words = [word for line in lines for word in line]
    return (
        min(word.x0 for word in words),
        min(word.y0 for word in words),
        max(word.x1 for word in words),
        max(word.y1 for word in words),
    )


def _data_lines(lines: list[list[Word]], header_index: int) -> tuple[list[list[Word]], int]:
    """Return data lines and the index of the first line after the table."""

    kept: list[list[Word]] = []
    previous_y = lines[header_index][0].y0
    cursor = header_index + 1
    while cursor < len(lines):
        line = lines[cursor]
        if _fixture_heading(line) or _panel_heading(line) is not None:
            break
        if line[0].y0 - previous_y > SCHEDULE_ROW_GAP_PT:
            break
        kept.append(line)
        previous_y = line[0].y0
        cursor += 1
    return kept, cursor


def _header_index(
    lines: list[list[Word]],
    start: int,
    accept,
) -> int | None:
    heading_y = lines[start][0].y0
    cursor = start + 1
    while cursor < len(lines) and lines[cursor][0].y0 - heading_y <= SCHEDULE_HEADER_WINDOW_PT:
        line = lines[cursor]
        if _fixture_heading(line) or _panel_heading(line) is not None:
            return None
        if accept(_match_headers(line, _aliases_for(accept))):
            return cursor
        cursor += 1
    return None


def _aliases_for(accept) -> tuple[tuple[str, str], ...]:
    if accept is _fixture_header:
        return _FIXTURE_ALIASES
    return _PANEL_ALIASES


def _region(kind: str, label: str, page: PageData, name: str, lines: list[list[Word]]) -> dict:
    x0, y0, x1, y1 = _bbox(lines)
    return {
        "kind": kind,
        "label": label,
        "page_index": page.index,
        "bbox": (x0, y0, x1, y1),
        "sheet": page.sheet,
        "name": name,
        "source": page.text_source,
    }


def parse_schedules(pages: list[PageData]) -> ParsedSchedules:
    parsed = ParsedSchedules()
    for page in pages:
        lines = group_lines(page.words)
        index = 0
        while index < len(lines):
            line = lines[index]
            if _fixture_heading(line):
                if _line_low(line):
                    parsed.ocr_checks.append(
                        _ocr_check("schedule", page, line, _line_text(line))
                    )
                    index += 1
                    continue
                index = _read_fixture_table(parsed, page, lines, index)
                continue
            panel_name = _panel_heading(line)
            if panel_name is not None:
                if _line_low(line):
                    parsed.ocr_checks.append(
                        _ocr_check("schedule", page, line, _line_text(line))
                    )
                    index += 1
                    continue
                if panel_name:
                    index = _read_panel_table(parsed, page, lines, index, panel_name)
                    continue
                # A panel schedule with no readable name is not a table.
            index += 1
    _index_fixtures(parsed)
    _index_panels(parsed)
    return parsed


def _read_fixture_table(parsed: ParsedSchedules, page: PageData, lines, start: int) -> int:
    header_index = _header_index(lines, start, _fixture_header)
    if header_index is None:
        return start + 1
    if _line_low(lines[header_index]):
        parsed.ocr_checks.append(
            _ocr_check("schedule", page, lines[header_index], _line_text(lines[header_index]))
        )
        return header_index + 1
    anchors = _match_headers(lines[header_index], _FIXTURE_ALIASES)
    spans = _spans(anchors)
    data, next_index = _data_lines(lines, header_index)
    block = [lines[start], lines[header_index], *data]
    for row in data:
        cells, withheld = _cells(row, spans)
        if withheld:
            parsed.ocr_checks.append(_ocr_check("schedule", page, row, _line_text(row)))
        if "mark" in withheld:
            continue
        mark = _mark_token(cells.get("mark", ""))
        if not mark:
            continue
        count_text = cells.get("count", "")
        parsed.fixture_rows.append(
            FixtureScheduleRow(
                mark=mark,
                description=cells.get("description", ""),
                manufacturer=cells.get("manufacturer", ""),
                lamp=cells.get("lamp", ""),
                voltage=cells.get("voltage", ""),
                mounting=cells.get("mounting", ""),
                mounting_height=cells.get("mounting_height", ""),
                count=whole_number(count_text),
                count_text=count_text,
                page_index=page.index,
                sheet=page.sheet,
                source=_row_source(page, row),
                ocr_check=bool(withheld),
            )
        )
    parsed.fixture_table_count += 1
    parsed.regions.append(_region("fixture", "fixture schedule", page, "", block))
    return next_index


def _read_panel_table(
    parsed: ParsedSchedules,
    page: PageData,
    lines,
    start: int,
    panel_name: str,
) -> int:
    header_index = _header_index(lines, start, _panel_header)
    if header_index is None:
        return start + 1
    if _line_low(lines[header_index]):
        parsed.ocr_checks.append(
            _ocr_check("schedule", page, lines[header_index], _line_text(lines[header_index]))
        )
        return header_index + 1
    anchors = _match_headers(lines[header_index], _PANEL_ALIASES)
    spans = _spans(anchors)
    data, next_index = _data_lines(lines, header_index)
    block = [lines[start], lines[header_index], *data]
    if panel_name not in parsed.panel_names:
        parsed.panel_names.append(panel_name)
    for row in data:
        cells, withheld = _cells(row, spans)
        if withheld:
            parsed.ocr_checks.append(_ocr_check("schedule", page, row, _line_text(row)))
        if "circuit" in withheld:
            continue
        circuit = normalize_circuit(cells.get("circuit", ""))
        if not circuit:
            continue
        parsed.panel_rows.append(
            PanelScheduleRow(
                panel=panel_name,
                circuit=circuit,
                description=cells.get("description", ""),
                breaker=cells.get("breaker", ""),
                poles=cells.get("poles", ""),
                load=cells.get("load", ""),
                page_index=page.index,
                sheet=page.sheet,
                source=_row_source(page, row),
                ocr_check=bool(withheld),
            )
        )
    parsed.panel_table_count += 1
    parsed.regions.append(
        _region("panel", "panel schedule", page, panel_name, block)
    )
    return next_index


def _index_fixtures(parsed: ParsedSchedules) -> None:
    counts: dict[str, int] = {}
    first: dict[str, FixtureScheduleRow] = {}
    for row in parsed.fixture_rows:
        counts[row.mark] = counts.get(row.mark, 0) + 1
        first.setdefault(row.mark, row)
    duplicates = [mark for mark, count in counts.items() if count > 1]
    parsed.duplicate_marks = sorted(duplicates)
    parsed.fixture_by_mark = {
        mark: row for mark, row in first.items() if mark not in duplicates
    }


def _index_panels(parsed: ParsedSchedules) -> None:
    counts: dict[tuple[str, str], int] = {}
    first: dict[tuple[str, str], PanelScheduleRow] = {}
    for row in parsed.panel_rows:
        key = (row.panel, row.circuit)
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, row)
    duplicates = [key for key, count in counts.items() if count > 1]
    parsed.duplicate_circuits = sorted(duplicates)
    parsed.panel_by_key = {key: row for key, row in first.items() if key not in duplicates}
