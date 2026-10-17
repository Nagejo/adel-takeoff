"""Discrepancy notes and the coordination / RFI log.

Schedule discrepancies already found by the takeoff are filed here with the
panel, riser, and scale comparisons. The log adds unlegended symbols,
unscheduled tags, notes the estimator marked RFI, and manual entries.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import fitz

from adel_takeoff.assemblies import data_dir_for_job
from adel_takeoff.constants import CONTRACTOR, DISCLAIMER
from adel_takeoff.extract import PageData, group_lines
from adel_takeoff.notes import (
    DISPOSITIONS,
    disposition_label,
    link_keynotes,
    parse_notes,
)
from adel_takeoff.scale import located_scale_readings, scales_agree


WHO_ANSWERS = ("Engineer", "Architect", "ID", "GC", "Owner")
STATUSES = ("open", "sent", "answered", "closed")
_PLAN_VS_SCHEDULE = {
    "plan count differs from schedule count",
    "schedule row with no plan marks",
    "mounting height differs",
    "type tag conflicts; left blank",
    "duplicate schedule rows; neither used",
    "duplicate panel schedule rows; neither used",
    "circuit on plan missing from panel schedule",
    "panel schedule not found",
}
_UNSCHEDULED = "type tag with no schedule row"
_PANEL_HEADING = re.compile(r"\b(?:PANELBOARD|PANEL)\s+SCHEDULE(?:\s+([A-Z]{1,6}\d{1,4}))?\b")
_HEADER_COUNT = re.compile(r"\b(\d{1,3})\s*(?:CIRCUITS|CIRCUIT|CKTS|CKT)\b", re.IGNORECASE)
_RISER_HEADING = re.compile(
    r"\b(?:ONE[-\s]?LINE(?:\s+DIAGRAM)?|SINGLE[-\s]?LINE(?:\s+DIAGRAM)?|RISER(?:\s+DIAGRAM)?)\b",
    re.IGNORECASE,
)
_PANEL_TOKEN = re.compile(r"\bPANEL(?:BOARD)?\s+([A-Z]{1,6}\d{1,4})\b")
_UNLEGENDED = re.compile(
    r"A mark on (?P<sheet>\S+) at \((?P<x>-?\d+(?:\.\d+)?), (?P<y>-?\d+(?:\.\d+)?)\) pt"
)
LOG_COLUMNS = (
    "id",
    "sheet",
    "location",
    "item",
    "question",
    "why",
    "who",
    "assumption",
    "status",
)


class CoordinationError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def notes_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "notes" / f"{job_id}.json"


def empty_store() -> dict:
    return {"dispositions": {}, "overrides": {}, "manual": []}


def load_store(path: Path) -> dict:
    if not path.exists():
        return empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty_store()
    if not isinstance(data, dict):
        return empty_store()
    dispositions = data.get("dispositions") if isinstance(data.get("dispositions"), dict) else {}
    overrides = data.get("overrides") if isinstance(data.get("overrides"), dict) else {}
    manual = data.get("manual") if isinstance(data.get("manual"), list) else []
    return {"dispositions": dispositions, "overrides": overrides, "manual": manual}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dispositions": {
            str(key): store["dispositions"][key]
            for key in sorted(store.get("dispositions") or {})
        },
        "manual": list(store.get("manual") or []),
        "overrides": {
            str(key): store["overrides"][key]
            for key in sorted(store.get("overrides") or {})
        },
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def notes_warning(notes: list[dict]) -> str:
    pending = [
        note
        for note in notes
        if note.get("kind") == "general" and not note.get("disposition")
    ]
    count = len(pending)
    if count == 0:
        return ""
    noun = "note" if count == 1 else "notes"
    return (
        f"Warning: {count} numbered general {noun} have no disposition. "
        "Set one before you rely on this export. The Notes Disposition sheet lists them."
    )


def apply_dispositions(notes: list[dict], store: dict) -> None:
    saved = store.get("dispositions") or {}
    for note in notes:
        value = saved.get(note["id"]) or ""
        note["disposition"] = value if value in {key for key, _label in DISPOSITIONS} else ""
        note["disposition_label"] = disposition_label(note["disposition"])


def drawing_discrepancies(
    pages: list[PageData],
    schedule_rows: list[dict],
    panel_rows: list[dict],
    pdf_bytes: bytes | None,
) -> list[dict]:
    """One log of plan/schedule, riser, scale, and panel-header differences."""

    found = [_from_schedule(row) for row in schedule_rows if row.get("kind") != _UNSCHEDULED]
    found.extend(_panel_header_notes(pages, panel_rows))
    found.extend(_riser_notes(pages, panel_rows))
    if pdf_bytes:
        found.extend(_scale_notes(pages, pdf_bytes))
    return found


def unscheduled_tags(schedule_rows: list[dict]) -> list[dict]:
    return [row for row in schedule_rows if row.get("kind") == _UNSCHEDULED]


def build_log(
    notes: list[dict],
    schedule_rows: list[dict],
    drawing_rows: list[dict],
    rfis: list[dict],
    store: dict,
) -> list[dict]:
    rows: list[dict] = []
    for rfi in rfis or []:
        if rfi.get("topic") != "Unlegended symbol":
            continue
        rows.append(_unlegended_row(rfi))
    for entry in unscheduled_tags(schedule_rows):
        rows.append(_unscheduled_row(entry))
    for index, entry in enumerate(drawing_rows, start=1):
        rows.append(_discrepancy_row(entry, index))
    for note in notes:
        if note.get("disposition") != "rfi":
            continue
        rows.append(_note_row(note))
    for entry in store.get("manual") or []:
        rows.append(_manual_row(entry))
    overrides = store.get("overrides") or {}
    for row in rows:
        saved = overrides.get(row["id"]) or {}
        for key in ("why", "who", "assumption", "status"):
            if saved.get(key):
                row[key] = saved[key]
    return rows


def coordination_packet(job: dict, rows: list[dict], pages: list[PageData], linked: dict, rfis) -> dict:
    notes = parse_notes(pages)
    link_keynotes(notes, rows)
    data_dir = data_dir_for_job(job)
    store = empty_store()
    if data_dir is not None and job.get("id"):
        store = load_store(notes_path(data_dir, job["id"]))
    apply_dispositions(notes, store)
    pdf_bytes = _pdf_bytes(job)
    drawing = drawing_discrepancies(
        pages,
        linked.get("discrepancies") or [],
        linked.get("panel_rows") or [],
        pdf_bytes,
    )
    return {
        "notes": notes,
        "notes_warning": notes_warning(notes),
        "drawing_discrepancies": drawing,
        "coordination": build_log(
            notes,
            linked.get("discrepancies") or [],
            drawing,
            rfis,
            store,
        ),
        "note_dispositions": DISPOSITIONS,
        "who_answers": WHO_ANSWERS,
        "coordination_statuses": STATUSES,
    }


def set_disposition(data_dir: Path, job_id: str, note_id: str, disposition: str, known_ids: set[str]) -> None:
    if note_id not in known_ids:
        raise CoordinationError(404, "That note is not on this drawing.")
    if disposition not in {key for key, _label in DISPOSITIONS}:
        raise CoordinationError(400, "Choose a disposition for this note.")
    path = notes_path(data_dir, job_id)
    store = load_store(path)
    store["dispositions"][note_id] = disposition
    save_store(path, store)


def add_manual(data_dir: Path, job_id: str, fields: dict) -> str:
    question = " ".join(str(fields.get("question") or "").split())
    if not question:
        raise CoordinationError(400, "A coordination item needs a question.")
    who = fields.get("who") or "Engineer"
    status = fields.get("status") or "open"
    if who not in WHO_ANSWERS:
        raise CoordinationError(400, "Who answers must be Engineer, Architect, ID, GC, or Owner.")
    if status not in STATUSES:
        raise CoordinationError(400, "Status must be open, sent, answered, or closed.")
    path = notes_path(data_dir, job_id)
    store = load_store(path)
    numbers = []
    for entry in store["manual"]:
        match = re.fullmatch(r"RFI-M-(\d+)", str(entry.get("id") or ""))
        if match:
            numbers.append(int(match.group(1)))
    entry_id = f"RFI-M-{max(numbers, default=0) + 1}"
    store["manual"].append(
        {
            "id": entry_id,
            "sheet": " ".join(str(fields.get("sheet") or "").split()),
            "location": " ".join(str(fields.get("location") or "").split()),
            "item": " ".join(str(fields.get("item") or "").split()),
            "question": question,
            "why": " ".join(str(fields.get("why") or "").split()),
            "who": who,
            "assumption": " ".join(str(fields.get("assumption") or "").split()),
            "status": status,
        }
    )
    save_store(path, store)
    return entry_id


def update_entry(data_dir: Path, job_id: str, entry_id: str, fields: dict) -> None:
    who = fields.get("who") or ""
    status = fields.get("status") or ""
    if who and who not in WHO_ANSWERS:
        raise CoordinationError(400, "Who answers must be Engineer, Architect, ID, GC, or Owner.")
    if status and status not in STATUSES:
        raise CoordinationError(400, "Status must be open, sent, answered, or closed.")
    path = notes_path(data_dir, job_id)
    store = load_store(path)
    manual = next((entry for entry in store["manual"] if entry.get("id") == entry_id), None)
    if manual is not None:
        if who:
            manual["who"] = who
        if status:
            manual["status"] = status
        if "why" in fields:
            manual["why"] = " ".join(str(fields.get("why") or "").split())
        if "assumption" in fields:
            manual["assumption"] = " ".join(str(fields.get("assumption") or "").split())
        save_store(path, store)
        return
    store["overrides"][entry_id] = {
        "why": " ".join(str(fields.get("why") or "").split()),
        "who": who,
        "assumption": " ".join(str(fields.get("assumption") or "").split()),
        "status": status or "open",
    }
    save_store(path, store)


def rfi_docx_bytes(job_name: str, rows: list[dict]) -> bytes:
    """A coordination log with the company name and no street address."""

    headers = (
        "ID",
        "Sheet",
        "Location",
        "Item",
        "Question",
        "Why it matters",
        "Who answers",
        "Suggested pricing assumption",
        "Status",
    )
    body = [
        _paragraph("Coordination and RFI log", bold=True),
        _paragraph(CONTRACTOR),
        _paragraph(f"Job: {job_name}"),
        _paragraph(DISCLAIMER),
        _paragraph(""),
        _table(headers, [_log_values(row) for row in rows]),
    ]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _pdf_bytes(job: dict) -> bytes | None:
    raw = job.get("pdf_path") or ""
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    return path.read_bytes()


def _from_schedule(row: dict) -> dict:
    kind = row.get("kind") or ""
    category = "plan vs schedule" if kind in _PLAN_VS_SCHEDULE or kind else "plan vs schedule"
    return {
        "category": category,
        "sheet": row.get("sheet_number") or "",
        "location": "",
        "item": kind,
        "detail": row.get("detail") or "",
        "plan": row.get("plan") if row.get("plan") is not None else "",
        "schedule": row.get("schedule") if row.get("schedule") is not None else "",
    }


def _panel_header_notes(pages: list[PageData], panel_rows: list[dict]) -> list[dict]:
    drawn = Counter((row.get("panel") or "").upper() for row in panel_rows if row.get("panel"))
    found = []
    for page in pages:
        lines = _page_lines(page)
        for index, line in enumerate(lines):
            match = _PANEL_HEADING.search(line["text"].upper())
            if match is None:
                continue
            panel = match.group(1) or ""
            window = " ".join(
                candidate["text"] for candidate in lines[index : index + 2]
            )
            count_match = _HEADER_COUNT.search(window)
            if count_match is None or not panel:
                continue
            stated = int(count_match.group(1))
            actual = int(drawn.get(panel, 0))
            if stated == actual:
                continue
            found.append(
                {
                    "category": "panel header vs drawn circuits",
                    "sheet": page.sheet,
                    "location": f"{line['x']}, {line['y']}",
                    "item": panel,
                    "detail": (
                        f"Panel {panel} header says {stated} circuits and the schedule draws {actual}."
                    ),
                    "plan": actual,
                    "schedule": stated,
                }
            )
    return found


def _riser_notes(pages: list[PageData], panel_rows: list[dict]) -> list[dict]:
    scheduled = sorted({(row.get("panel") or "").upper() for row in panel_rows if row.get("panel")})
    found = []
    for page in pages:
        lines = _page_lines(page)
        index = 0
        while index < len(lines):
            line = lines[index]
            if not _is_riser_heading(line["text"]):
                index += 1
                continue
            block = [line]
            last_y = line["y"]
            index += 1
            while index < len(lines):
                nxt = lines[index]
                if nxt["y"] - last_y > 48:
                    break
                if _PANEL_HEADING.search(nxt["text"].upper()):
                    break
                block.append(nxt)
                last_y = nxt["y"]
                index += 1
            names = []
            for entry in block:
                for match in _PANEL_TOKEN.finditer(entry["text"].upper()):
                    name = match.group(1)
                    if name not in names:
                        names.append(name)
            if not names:
                continue
            for name in names:
                if name in scheduled:
                    continue
                found.append(
                    {
                        "category": "panel schedule vs one-line/riser",
                        "sheet": page.sheet,
                        "location": f"{line['x']}, {line['y']}",
                        "item": name,
                        "detail": f"Panel {name} is on the one-line or riser and has no panel schedule.",
                        "plan": name,
                        "schedule": "",
                    }
                )
            for name in scheduled:
                if name in names:
                    continue
                found.append(
                    {
                        "category": "panel schedule vs one-line/riser",
                        "sheet": page.sheet,
                        "location": f"{line['x']}, {line['y']}",
                        "item": name,
                        "detail": f"Panel {name} has a panel schedule and is not on the one-line or riser.",
                        "plan": "",
                        "schedule": name,
                    }
                )
    return found


def _scale_notes(pages: list[PageData], pdf_bytes: bytes) -> list[dict]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    found = []
    try:
        for index, page in enumerate(document):
            height = float(page.rect.height)
            readings = located_scale_readings(page, index)
            title = [
                reading
                for reading in readings
                if reading["y"] < height * 0.14 or reading["y"] > height * 0.80
            ]
            view = [reading for reading in readings if reading not in title]
            if not title or not view:
                continue
            disagreed = [
                (left, right)
                for left in title
                for right in view
                if not scales_agree(Decimal(left["feet_per_point"]), Decimal(right["feet_per_point"]))
            ]
            if not disagreed:
                continue
            left, right = disagreed[0]
            sheet = pages[index].sheet if index < len(pages) else f"Page {index + 1}"
            found.append(
                {
                    "category": "title-block vs view scale",
                    "sheet": sheet,
                    "location": f"{left['x']}, {left['y']}",
                    "item": "scale",
                    "detail": (
                        f"Title-block scale {left['label']} and view scale {right['label']} disagree. "
                        "Lengths are not measured from either one."
                    ),
                    "plan": right["label"],
                    "schedule": left["label"],
                }
            )
    finally:
        document.close()
    return found


def _unlegended_row(rfi: dict) -> dict:
    detail = rfi.get("detail") or ""
    match = _UNLEGENDED.search(detail)
    sheet = match.group("sheet") if match else ""
    location = f"{match.group('x')}, {match.group('y')}" if match else ""
    identity = f"{sheet}-{location}".replace(" ", "")
    return _row(
        f"RFI-U-{identity or 'mark'}",
        sheet,
        location,
        "Unlegended symbol",
        detail,
        "An unlegended mark is not counted. The type has to be confirmed before it can be priced.",
        "Engineer",
        "Leave the mark uncounted until the legend names it.",
    )


def _unscheduled_row(entry: dict) -> dict:
    detail = entry.get("detail") or ""
    mark = ""
    match = re.search(r"type (\S+)", detail)
    if match:
        mark = match.group(1)
    return _row(
        f"RFI-T-{mark or 'tag'}-{(entry.get('sheet_number') or '').replace(' ', '')}",
        entry.get("sheet_number") or "",
        "",
        mark or "Unscheduled tag",
        detail,
        "A tag with no schedule row has no fixture type to price.",
        "Engineer",
        "Carry the plan count and confirm the type before pricing.",
    )


def _discrepancy_row(entry: dict, index: int) -> dict:
    category = entry.get("category") or "plan vs schedule"
    who = "Architect" if category == "title-block vs view scale" else "Engineer"
    why = {
        "plan vs schedule": "The plan and the schedule disagree. Price the plan count and qualify the schedule.",
        "panel schedule vs one-line/riser": "A panel named on one drawing and missing on the other can change feeder and gear scope.",
        "title-block vs view scale": "Two scales disagree, so a measured length would be wrong until the design team picks one.",
        "panel header vs drawn circuits": "The panel header and the drawn circuits disagree. Gear and breaker counts can change.",
    }.get(category, "This discrepancy can change cost.")
    slug = re.sub(r"[^a-z0-9]+", "-", category.casefold()).strip("-")
    item = entry.get("item") or category
    return _row(
        f"RFI-D-{index}-{slug}",
        entry.get("sheet") or "",
        entry.get("location") or "",
        item,
        entry.get("detail") or category,
        why,
        who,
        "",
    )


def _note_row(note: dict) -> dict:
    kind = "Keynote" if note.get("kind") == "keynote" else "General note"
    return _row(
        f"RFI-N-{note['id']}",
        note.get("sheet") or "",
        f"{note.get('x')}, {note.get('y')}",
        f"{kind} {note.get('number')}",
        note.get("text") or "",
        note.get("why") or "",
        note.get("who") or "Engineer",
        note.get("assumption") or "",
    )


def _manual_row(entry: dict) -> dict:
    return _row(
        entry.get("id") or "",
        entry.get("sheet") or "",
        entry.get("location") or "",
        entry.get("item") or "",
        entry.get("question") or "",
        entry.get("why") or "",
        entry.get("who") or "Engineer",
        entry.get("assumption") or "",
        entry.get("status") or "open",
    )


def _row(entry_id, sheet, location, item, question, why, who, assumption, status="open") -> dict:
    return {
        "id": entry_id,
        "sheet": sheet,
        "location": location,
        "item": item,
        "question": question,
        "why": why,
        "who": who,
        "assumption": assumption,
        "status": status,
    }


def _log_values(row: dict) -> list[str]:
    return [
        row.get("id") or "",
        row.get("sheet") or "",
        row.get("location") or "",
        row.get("item") or "",
        row.get("question") or "",
        row.get("why") or "",
        row.get("who") or "",
        row.get("assumption") or "",
        row.get("status") or "",
    ]


def _page_lines(page: PageData) -> list[dict]:
    found = []
    for words in group_lines(page.words):
        text = " ".join(word.text for word in words).strip()
        if not text:
            continue
        found.append(
            {
                "text": text,
                "x": round(sum(word.cx for word in words) / len(words), 2),
                "y": round(min(word.y0 for word in words), 2),
            }
        )
    return found


def _is_riser_heading(text: str) -> bool:
    if _RISER_HEADING.search(text) is None:
        return False
    words = text.split()
    return len(words) <= 6


def _xml(value) -> str:
    text = str(value if value is not None else "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _paragraph(text: str, bold: bool = False) -> str:
    run = f"<w:r>{'<w:rPr><w:b/></w:rPr>' if bold else ''}<w:t xml:space=\"preserve\">{_xml(text)}</w:t></w:r>"
    return f"<w:p>{run}</w:p>"


def _table(headers: tuple[str, ...], rows: list[list[str]]) -> str:
    def cells(values, header: bool) -> str:
        weight = "<w:rPr><w:b/></w:rPr>" if header else ""
        body = []
        for value in values:
            body.append(
                "<w:tc><w:p><w:r>"
                f"{weight}<w:t xml:space=\"preserve\">{_xml(value)}</w:t></w:r></w:p></w:tc>"
            )
        return "".join(body)

    rendered = [f"<w:tr>{cells(headers, True)}</w:tr>"]
    for row in rows:
        rendered.append(f"<w:tr>{cells(row, False)}</w:tr>")
    return f"<w:tbl>{''.join(rendered)}</w:tbl>"
