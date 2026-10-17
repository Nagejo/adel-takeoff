"""Review decisions stored beside the job.

The JSON file is the record a re-export reads. The same decision is written
onto the item row so the database and the file agree. Counts stay the
quantities the detector or the reviewer entered. This module does not
invent a quantity.
"""

from __future__ import annotations

import json
from pathlib import Path

from adel_takeoff.classify import categorize
from adel_takeoff.db import insert_manual_item, update_item_review
from adel_takeoff.links import MISSING, grid_for_bbox


REVIEW_ACTIONS = (
    "accept",
    "reject",
    "correct",
    "existing_to_remain",
    "nic",
    "by_others",
)
EXCLUSIONS = ("existing_to_remain", "nic", "by_others")
MARK_PAD = 6.0


class ReviewError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def review_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "reviews" / f"{job_id}.json"


def empty_document() -> dict:
    return {"added": [], "decisions": {}, "revision_checks": []}


def load_document(path: Path) -> dict:
    if not path.exists():
        return empty_document()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_document()
    decisions = data.get("decisions") or {}
    added = data.get("added") or []
    checks = data.get("revision_checks") or []
    if not isinstance(decisions, dict) or not isinstance(added, list) or not isinstance(checks, list):
        return empty_document()
    return {
        "added": added,
        "decisions": decisions,
        "revision_checks": [str(item_id) for item_id in checks],
    }


def save_document(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {
        "added": sorted(document.get("added") or [], key=lambda record: _id_sort(record["id"])),
        "decisions": {
            str(key): document["decisions"][key]
            for key in sorted(document.get("decisions") or {}, key=_id_sort)
        },
        "revision_checks": sorted(
            {str(item_id) for item_id in (document.get("revision_checks") or [])},
            key=_id_sort,
        ),
    }
    payload = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _id_sort(value) -> tuple:
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _decision(
    review_status: str,
    exclusion: str,
    corrected_label,
    corrected_panel,
    corrected_circuit,
) -> dict:
    return {
        "corrected_circuit": corrected_circuit,
        "corrected_label": corrected_label,
        "corrected_panel": corrected_panel,
        "exclusion": exclusion,
        "review_status": review_status,
    }


def decision_for_action(action: str, label: str, panel: str, circuit: str) -> dict:
    if action == "accept":
        return _decision("accepted", "", None, None, None)
    if action == "reject":
        return _decision("rejected", "", None, None, None)
    if action == "correct":
        cleaned = label.strip()
        if not cleaned:
            raise ReviewError(400, "A type correction needs a label.")
        return _decision(
            "corrected",
            "",
            cleaned,
            panel.strip(),
            circuit.strip(),
        )
    if action in EXCLUSIONS:
        return _decision("accepted", action, None, None, None)
    raise ReviewError(400, "Unknown review action.")


def _apply_decision(item: dict, decision: dict | None) -> None:
    if not decision:
        return
    item["review_status"] = decision.get("review_status") or "accepted"
    item["exclusion"] = decision.get("exclusion") or ""
    item["corrected_label"] = decision.get("corrected_label")
    item["corrected_panel"] = decision.get("corrected_panel")
    item["corrected_circuit"] = decision.get("corrected_circuit")


def synthetic_manual(record: dict) -> dict:
    x = float(record["x"])
    y = float(record["y"])
    sheet = record.get("sheet") or ""
    panel = record.get("panel") or ""
    circuit = record.get("circuit") or ""
    link_flag = record.get("link_flag") or ""
    return {
        "id": str(record["id"]),
        "label": record["label"],
        "category": record.get("category") or categorize(record["label"]),
        "measurement": "matched",
        "measured_quantity": 1,
        "sheets": [sheet] if sheet else [],
        "panel": panel,
        "circuit": circuit,
        "link_flag": link_flag,
        "deduped": False,
        "occurrences": [
            {
                "page_index": int(record["page_index"]),
                "sheet": sheet,
                "sheet_title": record.get("sheet_title") or "",
                "x0": x - MARK_PAD,
                "y0": y - MARK_PAD,
                "x1": x + MARK_PAD,
                "y1": y + MARK_PAD,
                "grid": record.get("grid") or "",
            }
        ],
        "override_quantity": None,
        "material_unit_cost": None,
        "labor_hours": None,
        "labor_rate_key": "journeyman",
        "method": "manual_add",
        "match_score": None,
        "review_status": record.get("review_status") or "accepted",
        "exclusion": record.get("exclusion") or "",
        "corrected_label": None,
        "corrected_panel": None,
        "corrected_circuit": None,
    }


def overlay_items(items: list[dict], document: dict) -> list[dict]:
    """Apply the review file. JSON wins when it names a decision."""

    decisions = document.get("decisions") or {}
    checks = {str(item_id) for item_id in (document.get("revision_checks") or [])}
    ordered: list[dict] = []
    seen: set[str] = set()
    for item in items:
        copied = dict(item)
        copied["occurrences"] = list(item.get("occurrences") or [])
        item_id = str(copied["id"])
        copied["id"] = item_id
        _apply_decision(copied, decisions.get(item_id))
        if item_id in checks:
            copied["revision_check"] = True
        seen.add(item_id)
        ordered.append(copied)
    for record in document.get("added") or []:
        item_id = str(record["id"])
        if item_id in seen:
            continue
        synthetic = synthetic_manual(record)
        _apply_decision(synthetic, decisions.get(item_id))
        ordered.append(synthetic)
        seen.add(item_id)
    return ordered


def _known_ids(items: list[dict], document: dict) -> list[str]:
    ids = [str(item["id"]) for item in items]
    ids.extend(str(record["id"]) for record in document.get("added") or [])
    ids.extend(str(key) for key in (document.get("decisions") or {}))
    return ids


def next_item_id(ids: list[str]) -> str:
    numbers = [int(value) for value in ids if str(value).isdigit()]
    return str(max(numbers, default=0) + 1)


def apply_decision(conn, data_dir: Path, job_id: str, item_id: str, action: str, label: str, panel: str, circuit: str) -> dict:
    from adel_takeoff.db import list_items

    items = list_items(conn, job_id)
    if not any(str(item["id"]) == str(item_id) for item in items):
        document = load_document(review_path(data_dir, job_id))
        if not any(str(record["id"]) == str(item_id) for record in document.get("added") or []):
            raise ReviewError(404, "Item not found")
    else:
        document = load_document(review_path(data_dir, job_id))
    decision = decision_for_action(action, label, panel, circuit)
    document.setdefault("decisions", {})[str(item_id)] = decision
    document["revision_checks"] = [
        check for check in document.get("revision_checks") or [] if str(check) != str(item_id)
    ]
    save_document(review_path(data_dir, job_id), document)
    update_item_review(conn, job_id, str(item_id), decision)
    return document


def add_missed_item(
    conn,
    data_dir: Path,
    job_id: str,
    *,
    pdf_bytes: bytes,
    page_index: int,
    x: float,
    y: float,
    label: str,
    panel: str,
    circuit: str,
) -> str:
    """Add one mark at a point the reviewer clicked.

    The quantity is one because one location was entered. The sheet name is
    read from that page. There is no detector score.
    """

    from adel_takeoff.db import list_items
    from adel_takeoff.extract import page_words, read_sheet_title, sheet_name
    import fitz

    cleaned = label.strip()
    if not cleaned:
        raise ReviewError(400, "A missed item needs a type label.")
    try:
        point_x = float(x)
        point_y = float(y)
    except (TypeError, ValueError) as exc:
        raise ReviewError(400, "Location x and y must be numbers.") from exc
    if point_x != point_x or point_y != point_y:
        raise ReviewError(400, "Location x and y must be numbers.")
    document_pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_index < 0 or page_index >= document_pdf.page_count:
            raise ReviewError(400, "That PDF page is not in this drawing set.")
        page = document_pdf[page_index]
        width = float(page.rect.width)
        height = float(page.rect.height)
        if not (0 <= point_x <= width and 0 <= point_y <= height):
            raise ReviewError(400, "That location is outside the sheet.")
        words = page_words(page)
        sheet = sheet_name(words, page_index)
        title = read_sheet_title(words)
    finally:
        document_pdf.close()

    panel_text = panel.strip()
    circuit_text = circuit.strip()
    if panel_text and circuit_text:
        link_flag = ""
    else:
        link_flag = MISSING
    grid = grid_for_bbox((point_x, point_y, point_x, point_y), words)
    items = list_items(conn, job_id)
    document = load_document(review_path(data_dir, job_id))
    item_id = next_item_id(_known_ids(items, document))
    record = {
        "category": categorize(cleaned),
        "circuit": circuit_text,
        "grid": grid,
        "id": item_id,
        "label": cleaned,
        "link_flag": link_flag,
        "method": "manual_add",
        "page_index": page_index,
        "panel": panel_text,
        "sheet": sheet,
        "sheet_title": title,
        "x": round(point_x, 2),
        "y": round(point_y, 2),
    }
    document.setdefault("added", []).append(record)
    save_document(review_path(data_dir, job_id), document)
    insert_manual_item(conn, job_id, synthetic_manual(record))
    return item_id
