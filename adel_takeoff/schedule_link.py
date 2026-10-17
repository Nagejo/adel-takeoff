"""Link device tags to schedule rows and record both sides of a mismatch.

Schedule text is copied from the matching row. A missing row leaves the
schedule fields blank. A repeated row is not used. Plan counts stay the
devices on the drawing. A printed count is recorded beside them and does
not replace them.
"""

from __future__ import annotations

import re
from collections import defaultdict

from adel_takeoff.extract import PageData
from adel_takeoff.links import is_grid_line, texts_near_bbox
from adel_takeoff.schedules import (
    ParsedSchedules,
    normalize_circuit,
    normalize_token,
    parse_schedules,
)


_TYPE_TAG = re.compile(r"\b(?:TYPE|MARK|FIXTURE)\s+([A-Z]{1,4}\d{0,3})\b", re.IGNORECASE)
_PANELISH = re.compile(
    r"\b(?:PANEL|HOMERUN)\b|\b[A-Z]{1,4}\d{1,2}\s*[-/]\s*\d{1,3}\b",
    re.IGNORECASE,
)
_HEIGHT_VALUE = r"(\d+\s*'\s*-?\s*\d+\s*\"?)"
_AFF_HEIGHT = re.compile(_HEIGHT_VALUE + r"\s*AFF\b", re.IGNORECASE)
_MOUNT_HEIGHT = re.compile(r"\bMOUNTING\s+HEIGHT\s+" + _HEIGHT_VALUE, re.IGNORECASE)
_FLOOR = re.compile(
    r"\b((?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|"
    r"GROUND|BASEMENT|ROOF|MEZZANINE)\s+FLOOR|LEVEL\s+\d+)\b",
    re.IGNORECASE,
)
_MARK = re.compile(r"[A-Z]{1,4}\d{0,3}")

_BLANK_FIELDS = (
    "type_mark",
    "type_description",
    "mounting",
    "mounting_height",
    "voltage",
    "circuit_description",
    "breaker",
    "poles",
    "schedule_load",
)


def floor_from_title(title: str) -> str:
    """A floor only when the sheet title prints one."""

    match = _FLOOR.search(title or "")
    if match is None:
        return ""
    return match.group(1).upper()


def _norm_height(value: str) -> str:
    text = value.strip().upper().replace("’", "'").replace("′", "'")
    text = text.replace("”", '"').replace("“", '"')
    return re.sub(r"\s+", "", text)


def _unique_text(values) -> list[str]:
    found: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in found:
            found.append(text)
    return found


def _unique_heights(values: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = _norm_height(cleaned)
        if key in seen:
            continue
        seen.add(key)
        found.append(cleaned)
    return found


def resolve_height(schedule_height: str, tag_heights: list[str]) -> tuple[str, bool]:
    """One height when the sources agree. Both texts when they do not.

    The returned flag is true when the displayed value records a difference
    instead of choosing a source.
    """

    tags = _unique_heights(tag_heights)
    schedule = schedule_height.strip()
    if schedule and len(tags) == 1 and _norm_height(tags[0]) == _norm_height(schedule):
        return schedule, False
    if schedule and tags:
        parts = [f"schedule: {schedule}"] + [f"tag: {tag}" for tag in tags]
        return "; ".join(parts), True
    if schedule:
        return schedule, False
    if len(tags) == 1:
        return tags[0], False
    if len(tags) > 1:
        return "; ".join(f"tag: {tag}" for tag in tags), True
    return "", False


def _apply_height(row: dict, schedule_height: str, tag_heights: list[str]) -> None:
    height, conflict = resolve_height(schedule_height, tag_heights)
    row["mounting_height"] = height
    row["schedule_height_conflict"] = conflict
    row["schedule_height_source"] = schedule_height.strip()
    row["tag_height_source"] = "; ".join(_unique_heights(tag_heights))


def _clear(row: dict) -> None:
    for key in _BLANK_FIELDS:
        row[key] = ""
    row["schedule_height_conflict"] = False
    row["schedule_type_conflict"] = False


def _texts_for_row(row: dict, pages: dict[int, PageData]) -> list[str]:
    texts: list[str] = []
    for occurrence in row.get("occurrences") or []:
        page = pages.get(int(occurrence["page_index"]))
        if page is None:
            continue
        bbox = (
            float(occurrence["x0"]),
            float(occurrence["y0"]),
            float(occurrence["x1"]),
            float(occurrence["y1"]),
        )
        for text in texts_near_bbox(bbox, page.words):
            if text not in texts:
                texts.append(text)
    return texts


def _explicit_types(texts: list[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        if is_grid_line(text):
            continue
        for match in _TYPE_TAG.finditer(text):
            mark = match.group(1).upper()
            if mark not in found:
                found.append(mark)
    return found


def _implicit_types(texts: list[str], known: set[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        if is_grid_line(text) or _PANELISH.search(text):
            continue
        for token in text.split():
            cleaned = normalize_token(token)
            if not _MARK.fullmatch(cleaned) or cleaned not in known:
                continue
            if cleaned not in found:
                found.append(cleaned)
    return found


def _height_tags(texts: list[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        for pattern in (_MOUNT_HEIGHT, _AFF_HEIGHT):
            for match in pattern.finditer(text):
                found.append(match.group(1).strip())
    return found


def _id_key(value: str) -> tuple:
    if str(value).isdigit():
        return (0, int(value))
    return (1, str(value))


def _item_ids(rows: list[dict]) -> list[str]:
    found = []
    for row in rows:
        item_id = str(row["id"])
        if item_id not in found:
            found.append(item_id)
    return sorted(found, key=_id_key)


def _sheet_numbers(rows: list[dict]) -> str:
    found: list[str] = []
    for row in rows:
        for sheet in row.get("sheet_numbers") or []:
            if sheet and sheet not in found:
                found.append(sheet)
        for occurrence in row.get("occurrences") or []:
            sheet = occurrence.get("sheet") or ""
            if sheet and sheet not in found:
                found.append(sheet)
    return ", ".join(found)


def _discrepancy(
    kind: str,
    detail: str,
    plan,
    schedule,
    rows: list[dict],
    sheet_number: str = "",
) -> dict:
    return {
        "kind": kind,
        "detail": detail,
        "plan": plan if plan is not None else "",
        "schedule": schedule if schedule is not None else "",
        "item_ids": _item_ids(rows),
        "sheet_number": sheet_number or _sheet_numbers(rows),
    }


def _schedule_count_value(row) -> int | str | None:
    if row is None:
        return None
    if row.count is not None:
        return row.count
    return row.count_text or None


def _counts_match(count: int | None, count_text: str, plan_count: int) -> bool:
    if count is not None:
        return count == plan_count
    if not count_text.strip():
        return True
    return False


def attach_schedules(rows: list[dict], pages: list[PageData]) -> dict:
    """Copy schedule cells onto rows and build discrepancy and rollup lists.

    Rows are updated in place. Effective quantities are left alone.
    """

    parsed = parse_schedules(pages) if pages else ParsedSchedules()
    by_index = {page.index: page for page in pages}
    known_marks = {row.mark for row in parsed.fixture_rows}
    items_by_type: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        _clear(row)
        texts = _texts_for_row(row, by_index)
        explicit = _explicit_types(texts)
        tags = _height_tags(texts)
        if len(explicit) > 1:
            row["schedule_type_conflict"] = True
            _apply_height(row, "", tags)
            continue
        if len(explicit) == 1:
            mark = explicit[0]
        else:
            implicit = _implicit_types(texts, known_marks)
            if len(implicit) > 1:
                row["schedule_type_conflict"] = True
                _apply_height(row, "", tags)
                continue
            mark = implicit[0] if implicit else ""
        if not mark:
            _apply_height(row, "", tags)
            continue
        row["type_mark"] = mark
        items_by_type[mark].append(row)
        if mark in parsed.duplicate_marks:
            _apply_height(row, "", tags)
            continue
        schedule_row = parsed.fixture_by_mark.get(mark)
        schedule_height = schedule_row.mounting_height if schedule_row else ""
        if schedule_row is not None:
            row["type_description"] = schedule_row.description
            row["mounting"] = schedule_row.mounting
            row["voltage"] = schedule_row.voltage
        _apply_height(row, schedule_height, tags)

    _link_panels(rows, parsed)
    discrepancies = _discrepancies(rows, parsed, items_by_type)
    return {
        "discrepancies": discrepancies,
        "by_type": _by_type(parsed, items_by_type),
        "fixture_rows": [
            row.as_dict(used=row.mark not in parsed.duplicate_marks)
            for row in parsed.fixture_rows
        ],
        "panel_rows": [
            row.as_dict(used=(row.panel, row.circuit) not in parsed.duplicate_circuits)
            for row in parsed.panel_rows
        ],
        "regions": list(parsed.regions),
        "fixture_table_count": parsed.fixture_table_count,
        "panel_table_count": parsed.panel_table_count,
    }


def _link_panels(rows: list[dict], parsed: ParsedSchedules) -> None:
    named = set(parsed.panel_names)
    for row in rows:
        panel = (row.get("panel") or "").strip().upper()
        circuit = normalize_circuit(row.get("circuit") or "")
        if not panel or panel not in named:
            continue
        if not circuit:
            continue
        key = (panel, circuit)
        if key in parsed.duplicate_circuits:
            continue
        schedule_row = parsed.panel_by_key.get(key)
        if schedule_row is None:
            continue
        row["circuit_description"] = schedule_row.description
        row["breaker"] = schedule_row.breaker
        row["poles"] = schedule_row.poles
        row["schedule_load"] = schedule_row.load


def _discrepancies(rows: list[dict], parsed: ParsedSchedules, items_by_type) -> list[dict]:
    found: list[dict] = []
    for row in sorted(rows, key=lambda item: _id_key(str(item["id"]))):
        if not row.get("schedule_type_conflict"):
            continue
        found.append(
            _discrepancy(
                "type tag conflicts; left blank",
                f"item {row['id']} has more than one type tag; none was used",
                "",
                "",
                [row],
            )
        )

    duplicate_counts: dict[str, list[str]] = defaultdict(list)
    for row in parsed.fixture_rows:
        if row.mark in parsed.duplicate_marks:
            duplicate_counts[row.mark].append(row.count_text)
    for mark in parsed.duplicate_marks:
        members = items_by_type.get(mark, [])
        plan_count = len({str(row["id"]) for row in members})
        counts = "; ".join(duplicate_counts[mark])
        found.append(
            _discrepancy(
                "duplicate schedule rows; neither used",
                (
                    f"type {mark} is on {len(duplicate_counts[mark])} fixture schedule rows "
                    f"with counts {counts}; plan count {plan_count}; neither row was used"
                ),
                plan_count,
                counts,
                members,
                sheet_number=_sheet_numbers(members) or _schedule_sheet(parsed, mark),
            )
        )

    for mark in sorted(items_by_type):
        if mark in parsed.fixture_by_mark or mark in parsed.duplicate_marks:
            continue
        members = items_by_type[mark]
        found.append(
            _discrepancy(
                "type tag with no schedule row",
                f"type {mark} has no fixture schedule row",
                len({str(row["id"]) for row in members}),
                "",
                members,
            )
        )

    for mark in sorted(parsed.fixture_by_mark):
        schedule_row = parsed.fixture_by_mark[mark]
        members = items_by_type.get(mark, [])
        plan_count = len({str(row["id"]) for row in members})
        if plan_count == 0 and (
            schedule_row.count is None or schedule_row.count != 0
        ):
            found.append(
                _discrepancy(
                    "schedule row with no plan marks",
                    f"type {mark} is on the fixture schedule and has no plan marks",
                    0,
                    _schedule_count_value(schedule_row) or "",
                    [],
                    sheet_number=schedule_row.sheet,
                )
            )
        if not _counts_match(schedule_row.count, schedule_row.count_text, plan_count):
            printed = schedule_row.count_text or ""
            if schedule_row.count is None:
                detail = (
                    f"type {mark} plan count {plan_count}; "
                    f"schedule count cell is {printed!r}, which is not a whole number"
                )
            else:
                detail = (
                    f"type {mark} plan count {plan_count}; schedule count {schedule_row.count}"
                )
            found.append(
                _discrepancy(
                    "plan count differs from schedule count",
                    detail,
                    plan_count,
                    _schedule_count_value(schedule_row) or "",
                    members,
                    sheet_number=_sheet_numbers(members) or schedule_row.sheet,
                )
            )

    for row in sorted(rows, key=lambda item: _id_key(str(item["id"]))):
        if not row.get("schedule_height_conflict"):
            continue
        mark = row.get("type_mark") or "untagged"
        found.append(
            _discrepancy(
                "mounting height differs",
                f"item {row['id']} type {mark} mounting height {row.get('mounting_height') or ''}",
                row.get("tag_height_source") or "",
                row.get("schedule_height_source") or "",
                [row],
            )
        )

    found.extend(_panel_discrepancies(rows, parsed))
    return found


def _schedule_sheet(parsed: ParsedSchedules, mark: str) -> str:
    for row in parsed.fixture_rows:
        if row.mark == mark and row.sheet:
            return row.sheet
    return ""


def _panel_discrepancies(rows: list[dict], parsed: ParsedSchedules) -> list[dict]:
    if not parsed.panel_names:
        return []
    named = set(parsed.panel_names)
    missing_panels: dict[str, list[dict]] = defaultdict(list)
    missing_circuits: dict[tuple[str, str], list[dict]] = defaultdict(list)
    duplicate_hits: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        panel = (row.get("panel") or "").strip().upper()
        circuit = normalize_circuit(row.get("circuit") or "")
        if not panel:
            continue
        if panel not in named:
            missing_panels[panel].append(row)
            continue
        if not circuit:
            continue
        key = (panel, circuit)
        if key in parsed.duplicate_circuits:
            duplicate_hits[key].append(row)
            continue
        if key not in parsed.panel_by_key:
            missing_circuits[key].append(row)
    found = []
    for key in sorted(parsed.duplicate_circuits):
        members = duplicate_hits.get(key, [])
        panel, circuit = key
        descriptions = [
            row.description
            for row in parsed.panel_rows
            if (row.panel, row.circuit) == key
        ]
        found.append(
            _discrepancy(
                "duplicate panel schedule rows; neither used",
                (
                    f"panel {panel} circuit {circuit} is on {len(descriptions)} "
                    f"panel schedule rows ({'; '.join(descriptions)}); neither row was used"
                ),
                len({str(row["id"]) for row in members}),
                "; ".join(descriptions),
                members,
            )
        )
    for key in sorted(missing_circuits):
        panel, circuit = key
        members = missing_circuits[key]
        found.append(
            _discrepancy(
                "circuit on plan missing from panel schedule",
                f"panel {panel} circuit {circuit} is on the plan and not on the panel schedule",
                len({str(row["id"]) for row in members}),
                "",
                members,
            )
        )
    for panel in sorted(missing_panels):
        members = missing_panels[panel]
        found.append(
            _discrepancy(
                "panel schedule not found",
                f"panel {panel} is on the plan and the drawing has other panel schedules",
                len({str(row["id"]) for row in members}),
                "",
                members,
            )
        )
    return found


def _by_type(parsed: ParsedSchedules, items_by_type: dict[str, list[dict]]) -> list[dict]:
    rollup = []
    for mark in sorted(items_by_type):
        members = items_by_type[mark]
        all_ids = _item_ids(members)
        schedule_row = parsed.fixture_by_mark.get(mark)
        per_sheet: dict[str, dict] = {}
        for row in members:
            for occurrence in row.get("occurrences") or []:
                sheet = occurrence.get("sheet") or ""
                if not sheet:
                    continue
                bucket = per_sheet.setdefault(
                    sheet,
                    {"ids": [], "title": occurrence.get("sheet_title") or ""},
                )
                if str(row["id"]) not in bucket["ids"]:
                    bucket["ids"].append(str(row["id"]))
                if not bucket["title"] and occurrence.get("sheet_title"):
                    bucket["title"] = occurrence["sheet_title"]
        for sheet in sorted(per_sheet):
            bucket = per_sheet[sheet]
            on_sheet = [
                row
                for row in members
                if any((occurrence.get("sheet") or "") == sheet for occurrence in row.get("occurrences") or [])
            ]
            heights = _unique_text(row.get("mounting_height") or "" for row in on_sheet)
            rollup.append(
                {
                    "type": mark,
                    "type_description": schedule_row.description if schedule_row else "",
                    "sheet_number": sheet,
                    "floor": floor_from_title(bucket["title"]),
                    "plan_count_on_sheet": len(bucket["ids"]),
                    "plan_count_all_sheets": len(all_ids),
                    "schedule_count": _schedule_count_value(schedule_row),
                    "voltage": schedule_row.voltage if schedule_row else "",
                    "mounting": schedule_row.mounting if schedule_row else "",
                    "mounting_height": "; ".join(heights),
                }
            )
    return rollup
