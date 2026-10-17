"""Compare a revised drawing set with the previous one.

Sheets match on sheet number. A unique printed title pairs a sheet whose
number changed. Alignment is a translation after a uniform scale when the
page proportions agree. A weak alignment is refused: that sheet gets no
colored overlay and no position-based count diff.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz

from adel_takeoff.constants import (
    ALIGNMENT_ASPECT_TOLERANCE,
    ALIGNMENT_MIN_AGREEMENT,
    ALIGNMENT_WINDOW_PT,
    PERSIST_DISTANCE_PT,
    UNCHANGED_MOVE_PT,
)
from adel_takeoff.extract import page_words, read_pdf, read_sheet_title, sheet_name
from adel_takeoff.schedules import parse_schedules
from adel_takeoff.takeoff import run_takeoff


_NUMBER = re.compile(r"[^A-Z0-9]")
_TITLE = re.compile(r"[^A-Z0-9]+")
_INK_MAX = 200
_DIFF_STRIDE = 2
_DIFF_TOLERANCE = 2
_MIN_CLOUD_PIXELS = 12
_CLOUD_CELL = 14
_TITLE_MIN = 3

ADDITION_COLOR = (0.12, 0.28, 0.86)
DELETION_COLOR = (0.82, 0.12, 0.12)
CLOUD_COLOR = (0.86, 0.42, 0.05)


def revision_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "revisions" / f"{job_id}.json"


def overlay_file(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "revisions" / f"{job_id}-overlay.pdf"


def empty_revision() -> dict:
    return {
        "compared": False,
        "note": "No previous drawing set was compared.",
        "sheets": [],
        "changes": [],
        "revision_checks": [],
    }


def load_revision(path: Path) -> dict:
    if not path.is_file():
        return empty_revision()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("compared"):
        return empty_revision()
    changes = data.get("changes") or []
    sheets = data.get("sheets") or []
    checks = data.get("revision_checks") or []
    if not isinstance(changes, list) or not isinstance(sheets, list):
        return empty_revision()
    return {
        "compared": True,
        "note": data.get("note") or "",
        "previous_name": data.get("previous_name") or "",
        "compared_at": data.get("compared_at") or "",
        "sheets": sheets,
        "changes": changes,
        "revision_checks": [str(item) for item in checks],
    }


def save_revision(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def normalize_number(value: str) -> str:
    return _NUMBER.sub("", (value or "").upper())


def normalize_title(value: str) -> str:
    return _TITLE.sub(" ", (value or "").upper()).strip()


def sheet_records(pdf_bytes: bytes) -> list[dict]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        records = []
        for index, page in enumerate(document):
            words = page_words(page)
            records.append(
                {
                    "page_index": index,
                    "number": sheet_name(words, index),
                    "title": read_sheet_title(words),
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                }
            )
        return records
    finally:
        document.close()


def match_sheets(old_sheets: list[dict], new_sheets: list[dict]) -> list[dict]:
    """Pair sheets by unique number, then by unique title.

    A duplicated number is not paired by number. A duplicated title is not
    paired by title. Leftovers are added or removed.
    """

    old_left = [dict(sheet) for sheet in old_sheets]
    new_left = [dict(sheet) for sheet in new_sheets]
    pairs: list[dict] = []

    def take_unique(key_fn, status_when_numbers_differ: str) -> None:
        nonlocal old_left, new_left
        old_groups: dict[str, list[dict]] = {}
        new_groups: dict[str, list[dict]] = {}
        for sheet in old_left:
            key = key_fn(sheet)
            if not key:
                continue
            old_groups.setdefault(key, []).append(sheet)
        for sheet in new_left:
            key = key_fn(sheet)
            if not key:
                continue
            new_groups.setdefault(key, []).append(sheet)
        used_old = set()
        used_new = set()
        for key, old_group in old_groups.items():
            new_group = new_groups.get(key) or []
            if len(old_group) != 1 or len(new_group) != 1:
                continue
            old_sheet = old_group[0]
            new_sheet = new_group[0]
            numbers_same = normalize_number(old_sheet.get("number") or "") == normalize_number(
                new_sheet.get("number") or ""
            )
            pairs.append(
                _pair(
                    old_sheet,
                    new_sheet,
                    "matched" if numbers_same else status_when_numbers_differ,
                )
            )
            used_old.add(id(old_sheet))
            used_new.add(id(new_sheet))
        old_left = [sheet for sheet in old_left if id(sheet) not in used_old]
        new_left = [sheet for sheet in new_left if id(sheet) not in used_new]

    take_unique(lambda sheet: normalize_number(sheet.get("number") or ""), "renumbered")
    take_unique(
        lambda sheet: _title_key(sheet.get("title") or ""),
        "renumbered",
    )
    for sheet in new_left:
        pairs.append(_pair(None, sheet, "added"))
    for sheet in old_left:
        pairs.append(_pair(sheet, None, "removed"))
    return pairs


def _title_key(title: str) -> str:
    key = normalize_title(title)
    if len(key) < _TITLE_MIN:
        return ""
    return key


def _pair(old_sheet: dict | None, new_sheet: dict | None, status: str) -> dict:
    return {
        "status": status,
        "old_page_index": None if old_sheet is None else old_sheet["page_index"],
        "new_page_index": None if new_sheet is None else new_sheet["page_index"],
        "old_number": "" if old_sheet is None else old_sheet.get("number") or "",
        "new_number": "" if new_sheet is None else new_sheet.get("number") or "",
        "old_title": "" if old_sheet is None else old_sheet.get("title") or "",
        "new_title": "" if new_sheet is None else new_sheet.get("title") or "",
        "alignment": None,
    }


def align_pages(old_pdf: bytes, new_pdf: bytes, sheets: list[dict]) -> list[dict]:
    """Attach an alignment to each matched sheet. Failures stay on the sheet."""

    old_doc = fitz.open(stream=old_pdf, filetype="pdf")
    new_doc = fitz.open(stream=new_pdf, filetype="pdf")
    try:
        aligned = []
        for sheet in sheets:
            copied = dict(sheet)
            if sheet["status"] in ("matched", "renumbered"):
                copied["alignment"] = _align_page(
                    old_doc[sheet["old_page_index"]],
                    new_doc[sheet["new_page_index"]],
                )
            aligned.append(copied)
        return aligned
    finally:
        old_doc.close()
        new_doc.close()


def _align_page(old_page, new_page) -> dict:
    old_w = float(old_page.rect.width)
    old_h = float(old_page.rect.height)
    new_w = float(new_page.rect.width)
    new_h = float(new_page.rect.height)
    if old_w <= 0 or old_h <= 0 or new_w <= 0 or new_h <= 0:
        return _refused("Alignment refused: the page has no size. No overlay was drawn.", 0.0)
    aspect_old = old_w / old_h
    aspect_new = new_w / new_h
    if abs(aspect_old - aspect_new) > ALIGNMENT_ASPECT_TOLERANCE * abs(aspect_old):
        return _refused(
            "Alignment refused: page proportions do not match "
            f"({old_w:.0f}x{old_h:.0f} vs {new_w:.0f}x{new_h:.0f}). No overlay was drawn.",
            0.0,
        )
    scale_x = new_w / old_w
    scale_y = new_h / old_h
    old_samples, width, height = _render(old_page, fitz.Matrix(scale_x, scale_y))
    new_samples, new_width, new_height = _render(new_page, fitz.Matrix(1, 1))
    width = min(width, new_width)
    height = min(height, new_height)
    old_cells = _ink_cells(old_samples, width, height, 4)
    new_cells = _ink_cells(new_samples, width, height, 4)
    if not old_cells or not new_cells:
        return _refused(
            "Alignment refused: the sheet has no ink to register. No overlay was drawn.",
            0.0,
            scale_x,
            scale_y,
        )
    cell_w = max(1, width // 4)
    cell_h = max(1, height // 4)
    dx_cell, dy_cell, _coarse = _best_shift(
        old_cells,
        set(old_cells),
        new_cells,
        set(new_cells),
        cell_w,
        cell_h,
        ALIGNMENT_WINDOW_PT // 4,
        1,
    )
    old_points = _ink_points(old_samples, width, height, 2)
    new_points = _ink_points(new_samples, width, height, 2)
    dx, dy, score = _best_shift(
        old_points,
        set(old_points),
        new_points,
        set(new_points),
        width,
        height,
        4,
        1,
        origin=(dx_cell * 4, dy_cell * 4),
    )
    if score < ALIGNMENT_MIN_AGREEMENT:
        return _refused(
            "Alignment refused: agreement "
            f"{score:.2f} is below {ALIGNMENT_MIN_AGREEMENT:.2f}. "
            "No overlay was drawn.",
            score,
            scale_x,
            scale_y,
        )
    return {
        "ok": True,
        "reason": "",
        "confidence": round(score, 4),
        "dx": dx,
        "dy": dy,
        "scale_x": scale_x,
        "scale_y": scale_y,
    }


def _refused(reason: str, score: float, scale_x: float = 1.0, scale_y: float = 1.0) -> dict:
    return {
        "ok": False,
        "reason": reason,
        "confidence": round(score, 4),
        "dx": 0,
        "dy": 0,
        "scale_x": scale_x,
        "scale_y": scale_y,
    }


def _render(page, matrix) -> tuple[bytes, int, int]:
    pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    return pixmap.samples, pixmap.width, pixmap.height


def _ink_cells(samples: bytes, width: int, height: int, stride: int) -> list[tuple[int, int]]:
    cells = []
    seen: set[tuple[int, int]] = set()
    for y in range(0, height, stride):
        row = y * width
        for x in range(0, width, stride):
            if samples[row + x] < _INK_MAX:
                cell = (x // stride, y // stride)
                if cell not in seen:
                    seen.add(cell)
                    cells.append(cell)
    return cells


def _ink_points(samples: bytes, width: int, height: int, stride: int) -> list[tuple[int, int]]:
    points = []
    for y in range(0, height, stride):
        row = y * width
        for x in range(0, width, stride):
            if samples[row + x] < _INK_MAX:
                points.append((x, y))
    return points


def _best_shift(old_points, old_set, new_points, new_set, width, height, window, step, origin=(0, 0)):
    best = (0, 0, -1.0)
    ox, oy = origin
    y = oy - window
    while y <= oy + window:
        x = ox - window
        while x <= ox + window:
            score = _agreement(old_points, old_set, new_points, new_set, x, y, width, height)
            if score > best[2]:
                best = (x, y, score)
            x += step
        y += step
    return best


def _agreement(old_points, old_set, new_points, new_set, dx: int, dy: int, width: int, height: int) -> float:
    forward = _hit_rate(old_points, new_set, dx, dy, width, height)
    backward = _hit_rate(new_points, old_set, -dx, -dy, width, height)
    return (forward + backward) / 2


def _hit_rate(points, other: set, dx: int, dy: int, width: int, height: int) -> float:
    if not points:
        return 0.0
    hits = 0
    used = 0
    for x, y in points:
        nx = x + dx
        ny = y + dy
        if nx < 0 or ny < 0 or nx >= width or ny >= height:
            continue
        used += 1
        if (nx, ny) in other:
            hits += 1
    if used == 0:
        return 0.0
    return hits / used


def build_overlay(old_pdf: bytes, new_pdf: bytes, sheets: list[dict]) -> bytes:
    """Colored additions and deletions only where alignment succeeded."""

    old_doc = fitz.open(stream=old_pdf, filetype="pdf")
    new_doc = fitz.open(stream=new_pdf, filetype="pdf")
    out = fitz.open()
    try:
        for sheet in sheets:
            status = sheet["status"]
            if status in ("matched", "renumbered"):
                alignment = sheet.get("alignment") or {}
                if not alignment.get("ok"):
                    _text_page(
                        out,
                        _sheet_label(sheet),
                        alignment.get("reason")
                        or "Alignment refused. No overlay was drawn.",
                    )
                    continue
                _diff_page(
                    out,
                    old_doc[sheet["old_page_index"]],
                    new_doc[sheet["new_page_index"]],
                    alignment,
                    _sheet_label(sheet),
                )
            elif status == "added":
                _text_page(
                    out,
                    sheet["new_number"] or "Sheet",
                    f"Sheet {sheet['new_number'] or sheet['new_title']} was added. "
                    "There is no previous sheet to diff.",
                )
            else:
                _text_page(
                    out,
                    sheet["old_number"] or "Sheet",
                    f"Sheet {sheet['old_number'] or sheet['old_title']} was removed.",
                )
        return out.tobytes()
    finally:
        out.close()
        old_doc.close()
        new_doc.close()


def _sheet_label(sheet: dict) -> str:
    number = sheet.get("new_number") or sheet.get("old_number") or "Sheet"
    if sheet["status"] == "renumbered":
        return f"{sheet['old_number']} to {sheet['new_number']}"
    return number


def _text_page(out, title: str, message: str) -> None:
    page = out.new_page(width=612, height=792)
    page.insert_text(fitz.Point(48, 72), title, fontsize=16, fontname="helv")
    page.insert_textbox(
        fitz.Rect(48, 96, 540, 360),
        message,
        fontsize=12,
        fontname="helv",
    )


def _diff_page(out, old_page, new_page, alignment: dict, title: str) -> None:
    scale_x = float(alignment["scale_x"])
    scale_y = float(alignment["scale_y"])
    dx = int(alignment["dx"])
    dy = int(alignment["dy"])
    new_w = float(new_page.rect.width)
    new_h = float(new_page.rect.height)
    old_samples, width, height = _render(old_page, fitz.Matrix(scale_x, scale_y))
    new_samples, new_width, new_height = _render(new_page, fitz.Matrix(1, 1))
    width = min(width, new_width, int(new_w))
    height = min(height, new_height, int(new_h))
    old_points = _ink_points(old_samples, width, height, _DIFF_STRIDE)
    new_points = _ink_points(new_samples, width, height, _DIFF_STRIDE)
    shifted_old = {(x + dx, y + dy) for x, y in old_points}
    new_set = set(new_points)
    additions = [point for point in new_points if not _near(point, shifted_old)]
    deletions = [point for point in shifted_old if _inside(point, width, height) and not _near(point, new_set)]
    page = out.new_page(width=new_w, height=new_h)
    page.show_pdf_page(page.rect, new_page.parent, new_page.number)
    _paint(page, deletions, DELETION_COLOR)
    _paint(page, additions, ADDITION_COLOR)
    boxes = _clusters(additions + deletions)
    for box in boxes:
        _cloud(page, box)
    _legend(page, title, alignment)


def _inside(point: tuple[int, int], width: int, height: int) -> bool:
    x, y = point
    return 0 <= x < width and 0 <= y < height


def _near(point: tuple[int, int], other: set[tuple[int, int]]) -> bool:
    x, y = point
    for oy in range(y - _DIFF_TOLERANCE, y + _DIFF_TOLERANCE + 1):
        for ox in range(x - _DIFF_TOLERANCE, x + _DIFF_TOLERANCE + 1):
            if (ox, oy) in other:
                return True
    return False


def _paint(page, points: list[tuple[int, int]], color: tuple[float, float, float]) -> None:
    if not points:
        return
    runs: dict[int, list[tuple[int, int]]] = {}
    for x, y in points:
        runs.setdefault(y, []).append((x, x + _DIFF_STRIDE))
    shape = page.new_shape()
    for y, spans in runs.items():
        spans.sort()
        merged = [list(spans[0])]
        for start, end in spans[1:]:
            if start <= merged[-1][1] + _DIFF_STRIDE:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            shape.draw_rect(fitz.Rect(start, y, end, y + _DIFF_STRIDE))
    shape.finish(color=None, fill=color, fill_opacity=0.55, width=0)
    shape.commit()


def _clusters(points: list[tuple[int, int]]) -> list[tuple[float, float, float, float]]:
    if not points:
        return []
    cells: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for x, y in points:
        cells.setdefault((x // _CLOUD_CELL, y // _CLOUD_CELL), []).append((x, y))
    parent = {key: key for key in cells}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for cx, cy in list(cells):
        for nx in range(cx - 1, cx + 2):
            for ny in range(cy - 1, cy + 2):
                if (nx, ny) in parent:
                    left, right = find((cx, cy)), find((nx, ny))
                    if left != right:
                        parent[right] = left
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for key, members in cells.items():
        groups.setdefault(find(key), []).extend(members)
    boxes = []
    for members in groups.values():
        if len(members) < _MIN_CLOUD_PIXELS:
            continue
        xs = [point[0] for point in members]
        ys = [point[1] for point in members]
        boxes.append(
            (
                float(min(xs) - 4),
                float(min(ys) - 4),
                float(max(xs) + _DIFF_STRIDE + 4),
                float(max(ys) + _DIFF_STRIDE + 4),
            )
        )
    return boxes


def _cloud(page, box: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = box
    if x1 - x0 < 8:
        x1 = x0 + 8
    if y1 - y0 < 8:
        y1 = y0 + 8
    shape = page.new_shape()
    step = 16
    bulge = 7
    _edge(shape, x0, y0, x1, y0, 0, -1, step, bulge)
    _edge(shape, x1, y0, x1, y1, 1, 0, step, bulge)
    _edge(shape, x1, y1, x0, y1, 0, 1, step, bulge)
    _edge(shape, x0, y1, x0, y0, -1, 0, step, bulge)
    shape.finish(color=CLOUD_COLOR, width=1.3, closePath=False)
    shape.commit()


def _edge(shape, x0, y0, x1, y1, outward_x, outward_y, step, bulge) -> None:
    length = abs(x1 - x0) + abs(y1 - y0)
    if length <= 0:
        return
    steps = max(1, int(length // step))
    for index in range(steps):
        start_t = index / steps
        end_t = (index + 1) / steps
        sx = x0 + (x1 - x0) * start_t
        sy = y0 + (y1 - y0) * start_t
        ex = x0 + (x1 - x0) * end_t
        ey = y0 + (y1 - y0) * end_t
        cx = (sx + ex) / 2 + outward_x * bulge
        cy = (sy + ey) / 2 + outward_y * bulge
        shape.draw_curve(fitz.Point(sx, sy), fitz.Point(cx, cy), fitz.Point(ex, ey))


def _legend(page, title: str, alignment: dict) -> None:
    width = float(page.rect.width)
    rect = fitz.Rect(8, 8, min(width - 8, 280), 36)
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0.75, 0.72, 0.66), fill=(1, 1, 1), fill_opacity=0.92, width=0.4)
    shape.commit()
    page.insert_text(
        fitz.Point(12, 20),
        f"{title}: blue added, red removed",
        fontsize=8,
        fontname="helv",
    )
    page.insert_text(
        fitz.Point(12, 31),
        f"Aligned, agreement {alignment['confidence']:.2f}",
        fontsize=8,
        fontname="helv",
    )


def _item_dict(item) -> dict:
    if isinstance(item, dict):
        occurrences = item.get("occurrences") or []
        return {
            "id": str(item.get("id") or item.get("item_id") or ""),
            "label": item.get("label") or "",
            "panel": item.get("panel") or "",
            "circuit": item.get("circuit") or "",
            "measurement": item.get("measurement") or "",
            "measured_quantity": item.get("measured_quantity"),
            "occurrences": occurrences,
            "override_quantity": item.get("override_quantity"),
            "material_unit_cost": item.get("material_unit_cost"),
            "labor_hours": item.get("labor_hours"),
            "labor_rate_key": item.get("labor_rate_key") or "journeyman",
        }
    return {
        "id": str(item.item_id),
        "label": item.label,
        "panel": item.panel or "",
        "circuit": item.circuit or "",
        "measurement": item.measurement,
        "measured_quantity": item.measured_quantity,
        "occurrences": [occurrence.as_dict() for occurrence in item.occurrences],
        "override_quantity": None,
        "material_unit_cost": None,
        "labor_hours": None,
        "labor_rate_key": "journeyman",
    }


def _matchable(item: dict) -> bool:
    return bool(item.get("occurrences"))


def _in_counts(item: dict) -> bool:
    return _matchable(item) and item.get("measurement") != "unmatched"


def _quantity(item: dict) -> int:
    value = item.get("measured_quantity")
    if value is None:
        return 1
    return int(value)


def _center(occurrence: dict) -> tuple[float, float]:
    return (
        (float(occurrence["x0"]) + float(occurrence["x1"])) / 2,
        (float(occurrence["y0"]) + float(occurrence["y1"])) / 2,
    )


def _area(occurrence: dict) -> str:
    grid = (occurrence.get("grid") or "").strip()
    return grid or "ungridded"


def _map_point(x: float, y: float, alignment: dict) -> tuple[float, float]:
    return (
        x * float(alignment["scale_x"]) + float(alignment["dx"]),
        y * float(alignment["scale_y"]) + float(alignment["dy"]),
    )


def diff_counts(old_items, new_items, sheets: list[dict], decisions: dict | None = None) -> dict:
    """Count deltas and id carry-forward. Refused sheets are not position-matched."""

    old_rows = [row for item in old_items if _matchable(row := _item_dict(item))]
    new_rows = [row for item in new_items if _matchable(row := _item_dict(item))]
    decisions = {str(key): value for key, value in (decisions or {}).items()}
    links = []
    used_old: set[str] = set()
    used_new: set[str] = set()
    refused_new_pages = set()
    changes = _sheet_change_rows(sheets)

    for sheet in sheets:
        alignment = sheet.get("alignment") or {}
        if sheet["status"] in ("matched", "renumbered") and not alignment.get("ok"):
            if sheet["new_page_index"] is not None:
                refused_new_pages.add(sheet["new_page_index"])
            changes.append(
                _change_row(
                    sheet=sheet.get("new_number") or sheet.get("old_number") or "",
                    item="alignment",
                    tag=sheet.get("new_title") or sheet.get("old_title") or "",
                    area="",
                    old_qty=None,
                    new_qty=None,
                    delta=None,
                    source="alignment",
                    old_ids="",
                    new_ids="",
                    note=(alignment.get("reason") or "Alignment refused.")
                    + " Item counts on this sheet were not compared.",
                )
            )
            continue
        if sheet["status"] not in ("matched", "renumbered") or not alignment.get("ok"):
            continue
        old_group = [item for item in old_rows if _on_page(item, sheet["old_page_index"])]
        new_group = [item for item in new_rows if _on_page(item, sheet["new_page_index"])]
        candidates = []
        for old_item in old_group:
            old_point = _map_point(*_center(old_item["occurrences"][0]), alignment)
            for new_item in new_group:
                if _label(old_item) != _label(new_item):
                    continue
                new_point = _center(new_item["occurrences"][0])
                distance = _distance(old_point, new_point)
                if distance <= PERSIST_DISTANCE_PT:
                    candidates.append((distance, old_item, new_item))
        candidates.sort(key=lambda item: (item[0], item[1]["id"], item[2]["id"]))
        for distance, old_item, new_item in candidates:
            if old_item["id"] in used_old or new_item["id"] in used_new:
                continue
            used_old.add(old_item["id"])
            used_new.add(new_item["id"])
            unchanged = distance <= UNCHANGED_MOVE_PT and _same_tags(old_item, new_item)
            links.append(
                {
                    "old_id": old_item["id"],
                    "new_id": new_item["id"],
                    "unchanged": unchanged,
                    "distance": round(distance, 2),
                    "sheet": sheet.get("new_number") or "",
                    "label": _label(new_item),
                    "area": _area(new_item["occurrences"][0]),
                    "old_item": old_item,
                    "new_item": new_item,
                }
            )

    removed = [
        item
        for item in old_rows
        if item["id"] not in used_old and not _page_refused_old(item, sheets) and _in_counts(item)
    ]
    added = [
        item
        for item in new_rows
        if item["id"] not in used_new
        and item["occurrences"][0]["page_index"] not in refused_new_pages
        and _in_counts(item)
    ]
    id_map, checks, kept = _assign_ids(new_rows, links, decisions)
    changes.extend(_count_rows(links, removed, added, id_map))
    carried_costs = {}
    for link in links:
        if not link["unchanged"] or not _in_counts(link["old_item"]):
            continue
        carried_costs[id_map[link["new_id"]]] = {
            "override_quantity": link["old_item"].get("override_quantity"),
            "material_unit_cost": link["old_item"].get("material_unit_cost"),
            "labor_hours": link["old_item"].get("labor_hours"),
            "labor_rate_key": link["old_item"].get("labor_rate_key") or "journeyman",
        }
    return {
        "changes": changes,
        "links": [
            {key: value for key, value in link.items() if key not in ("old_item", "new_item")}
            for link in links
        ],
        "id_map": id_map,
        "kept_decisions": kept,
        "revision_checks": checks,
        "carried_costs": carried_costs,
    }


def _on_page(item: dict, page_index: int | None) -> bool:
    if page_index is None:
        return False
    return int(item["occurrences"][0]["page_index"]) == int(page_index)


def _page_refused_old(item: dict, sheets: list[dict]) -> bool:
    page_index = int(item["occurrences"][0]["page_index"])
    for sheet in sheets:
        if sheet.get("old_page_index") != page_index:
            continue
        if sheet["status"] not in ("matched", "renumbered"):
            return False
        alignment = sheet.get("alignment") or {}
        return not alignment.get("ok")
    return False


def _label(item: dict) -> str:
    return (item.get("label") or "").strip().upper()


def _same_tags(old_item: dict, new_item: dict) -> bool:
    return (
        (old_item.get("panel") or "").strip().upper() == (new_item.get("panel") or "").strip().upper()
        and (old_item.get("circuit") or "").strip().upper()
        == (new_item.get("circuit") or "").strip().upper()
    )


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5


def _assign_ids(new_rows, links, decisions) -> tuple[dict, list[str], dict]:
    link_by_new = {link["new_id"]: link for link in links}
    used: set[str] = set()
    id_map: dict[str, str] = {}
    for link in links:
        id_map[link["new_id"]] = _claim(used, link["old_id"])
    for item in new_rows:
        if item["id"] in id_map:
            continue
        id_map[item["id"]] = _claim(used, item["id"])
    kept = {}
    checks = []
    for item in new_rows:
        final = id_map[item["id"]]
        link = link_by_new.get(item["id"])
        if link and link["unchanged"]:
            decision = decisions.get(link["old_id"])
            if decision:
                kept[final] = decision
            continue
        checks.append(final)
    return id_map, checks, kept


def _claim(used: set[str], preferred: str) -> str:
    if preferred and preferred not in used:
        used.add(preferred)
        return preferred
    number = 1
    while str(number) in used:
        number += 1
    chosen = str(number)
    used.add(chosen)
    return chosen


def _sheet_change_rows(sheets: list[dict]) -> list[dict]:
    rows = []
    for sheet in sheets:
        if sheet["status"] == "added":
            rows.append(
                _change_row(
                    sheet=sheet.get("new_number") or "",
                    item="sheet",
                    tag=sheet.get("new_title") or "",
                    area="",
                    old_qty=None,
                    new_qty=None,
                    delta=None,
                    source="sheet",
                    old_ids="",
                    new_ids="",
                    note="added",
                )
            )
        elif sheet["status"] == "removed":
            rows.append(
                _change_row(
                    sheet=sheet.get("old_number") or "",
                    item="sheet",
                    tag=sheet.get("old_title") or "",
                    area="",
                    old_qty=None,
                    new_qty=None,
                    delta=None,
                    source="sheet",
                    old_ids="",
                    new_ids="",
                    note="removed",
                )
            )
        elif sheet["status"] == "renumbered":
            rows.append(
                _change_row(
                    sheet=sheet.get("new_number") or "",
                    item="sheet",
                    tag=sheet.get("new_title") or sheet.get("old_title") or "",
                    area="",
                    old_qty=None,
                    new_qty=None,
                    delta=None,
                    source="sheet",
                    old_ids="",
                    new_ids="",
                    note=f"renumbered from {sheet.get('old_number') or ''} to {sheet.get('new_number') or ''}",
                )
            )
    return rows


def _count_rows(links, removed, added, id_map: dict) -> list[dict]:
    buckets: dict[tuple, dict] = {}

    def bucket(sheet: str, label: str, area: str) -> dict:
        key = (sheet, label, area)
        if key not in buckets:
            buckets[key] = {
                "sheet": sheet,
                "label": label,
                "area": area,
                "old_qty": 0,
                "new_qty": 0,
                "old_ids": [],
                "new_ids": [],
                "notes": [],
            }
        return buckets[key]

    for link in links:
        old_item = link["old_item"]
        new_item = link["new_item"]
        if not _in_counts(old_item) and not _in_counts(new_item):
            continue
        row = bucket(link["sheet"], link["label"], link["area"])
        row["old_qty"] += _quantity(old_item)
        row["new_qty"] += _quantity(new_item)
        row["old_ids"].append(old_item["id"])
        row["new_ids"].append(id_map.get(link["new_id"], link["new_id"]))
        if not link["unchanged"]:
            notes = []
            if link["distance"] > UNCHANGED_MOVE_PT:
                notes.append(f"moved {link['distance']:.0f} pt")
            if not _same_tags(old_item, new_item):
                notes.append(
                    "panel/circuit "
                    f"{old_item.get('panel') or ''}/{old_item.get('circuit') or ''} "
                    f"to {new_item.get('panel') or ''}/{new_item.get('circuit') or ''}"
                )
            row["notes"].append(f"{old_item['id']}: " + ", ".join(notes))
    for item in removed:
        occurrence = item["occurrences"][0]
        row = bucket(occurrence.get("sheet") or "", _label(item), _area(occurrence))
        row["old_qty"] += _quantity(item)
        row["old_ids"].append(item["id"])
        row["notes"].append(f"{item['id']}: removed")
    for item in added:
        occurrence = item["occurrences"][0]
        row = bucket(occurrence.get("sheet") or "", _label(item), _area(occurrence))
        final_id = id_map.get(item["id"], item["id"])
        row["new_qty"] += _quantity(item)
        row["new_ids"].append(final_id)
        row["notes"].append(f"{final_id}: added")

    rows = []
    for row in buckets.values():
        delta = row["new_qty"] - row["old_qty"]
        if delta == 0 and not row["notes"]:
            continue
        rows.append(
            _change_row(
                sheet=row["sheet"],
                item="takeoff",
                tag=row["label"],
                area=row["area"],
                old_qty=row["old_qty"],
                new_qty=row["new_qty"],
                delta=delta,
                source="takeoff",
                old_ids=", ".join(row["old_ids"]),
                new_ids=", ".join(row["new_ids"]),
                note="; ".join(note for note in row["notes"] if note),
            )
        )
    return rows


def _change_row(**fields) -> dict:
    return {
        "sheet": fields["sheet"],
        "item": fields["item"],
        "tag": fields["tag"],
        "area": fields["area"],
        "old_qty": fields["old_qty"],
        "new_qty": fields["new_qty"],
        "delta": fields["delta"],
        "source": fields["source"],
        "old_item_ids": fields["old_ids"],
        "new_item_ids": fields["new_ids"],
        "note": fields["note"],
    }


def diff_schedules(old_pdf: bytes, new_pdf: bytes) -> list[dict]:
    old_parsed = parse_schedules(read_pdf(old_pdf))
    new_parsed = parse_schedules(read_pdf(new_pdf))
    rows = []
    rows.extend(
        _table_diff(
            old_parsed.fixture_by_mark,
            new_parsed.fixture_by_mark,
            key_text=lambda key: key,
            fields=(
                "description",
                "manufacturer",
                "lamp",
                "voltage",
                "mounting",
                "mounting_height",
            ),
            source="fixture schedule",
            item="fixture schedule",
        )
    )
    rows.extend(
        _table_diff(
            old_parsed.panel_by_key,
            new_parsed.panel_by_key,
            key_text=lambda key: f"{key[0]} / {key[1]}",
            fields=("description", "breaker", "poles", "load"),
            source="panel schedule",
            item="panel schedule",
        )
    )
    return rows


def _table_diff(old_map, new_map, *, key_text, fields, source: str, item: str) -> list[dict]:
    rows = []
    keys = list(dict.fromkeys([*old_map.keys(), *new_map.keys()]))
    for key in keys:
        old_row = old_map.get(key)
        new_row = new_map.get(key)
        old_count = None if old_row is None else getattr(old_row, "count", None)
        new_count = None if new_row is None else getattr(new_row, "count", None)
        notes = []
        if old_row is None:
            notes.append("added")
        elif new_row is None:
            notes.append("removed")
        else:
            for field in fields:
                old_value = getattr(old_row, field) or ""
                new_value = getattr(new_row, field) or ""
                if old_value != new_value:
                    notes.append(f"{field} {old_value or 'blank'} to {new_value or 'blank'}")
            if old_count != new_count:
                notes.append(f"count {old_count if old_count is not None else 'blank'} to {new_count if new_count is not None else 'blank'}")
        if not notes:
            continue
        delta = None
        if isinstance(old_count, int) or isinstance(new_count, int):
            delta = (new_count or 0) - (old_count or 0)
        sheet = ""
        if new_row is not None:
            sheet = new_row.sheet
        elif old_row is not None:
            sheet = old_row.sheet
        rows.append(
            _change_row(
                sheet=sheet,
                item=item,
                tag=key_text(key),
                area="",
                old_qty=old_count,
                new_qty=new_count,
                delta=delta,
                source=source,
                old_ids="",
                new_ids="",
                note="; ".join(notes),
            )
        )
    return rows


def compare_sets(old_pdf: bytes, new_pdf: bytes, old_items, decisions: dict | None = None) -> dict:
    """Compare the two PDFs. The returned takeoff is the revised set."""

    sheets = align_pages(old_pdf, new_pdf, match_sheets(sheet_records(old_pdf), sheet_records(new_pdf)))
    result = run_takeoff(new_pdf)
    counted = diff_counts(old_items, result.items, sheets, decisions)
    used = set(counted["id_map"].values())
    unchanged_ids = set(counted["kept_decisions"]) | {
        counted["id_map"][link["new_id"]]
        for link in counted["links"]
        if link["unchanged"] and link["new_id"] in counted["id_map"]
    }
    for item in result.items:
        if item.item_id in counted["id_map"]:
            continue
        final = _claim(used, item.item_id)
        counted["id_map"][item.item_id] = final
        if final not in unchanged_ids:
            counted["revision_checks"].append(final)
    changes = list(counted["changes"])
    changes.extend(diff_schedules(old_pdf, new_pdf))
    for item in result.items:
        item.item_id = counted["id_map"].get(item.item_id, item.item_id)
    return {
        "sheets": [_public_sheet(sheet) for sheet in sheets],
        "changes": changes,
        "id_map": counted["id_map"],
        "kept_decisions": counted["kept_decisions"],
        "revision_checks": counted["revision_checks"],
        "carried_costs": counted["carried_costs"],
        "takeoff": result,
        "overlay_sheets": sheets,
    }


def _public_sheet(sheet: dict) -> dict:
    alignment = sheet.get("alignment") or None
    public = {
        "status": sheet["status"],
        "old_page_index": sheet["old_page_index"],
        "new_page_index": sheet["new_page_index"],
        "old_number": sheet["old_number"],
        "new_number": sheet["new_number"],
        "old_title": sheet["old_title"],
        "new_title": sheet["new_title"],
        "alignment": None,
    }
    if alignment is not None:
        public["alignment"] = {
            "ok": bool(alignment.get("ok")),
            "reason": alignment.get("reason") or "",
            "confidence": alignment.get("confidence") or 0,
            "dx": alignment.get("dx") or 0,
            "dy": alignment.get("dy") or 0,
        }
    return public


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
