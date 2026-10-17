"""Classify each sheet and record which downstream steps use it.

The class comes from the title block (sheet number and title, PDF text or
OCR) and from layout cues: a table, a column grid, and headings in the
body. The estimator can override the class. Low-confidence OCR is not
applied. Nothing here imports a conduit router.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adel_takeoff.extract import PageData, Word, group_lines, is_low_ocr


SHEET_CLASSES = (
    "fixture_schedule",
    "panel_schedule",
    "riser",
    "legend",
    "cover",
    "notes",
    "site",
    "details",
    "systems_plan",
    "lighting_plan",
    "power_plan",
    "unknown",
)
CLASS_LABELS = {
    "power_plan": "Power plan",
    "lighting_plan": "Lighting plan",
    "systems_plan": "Systems / LV plan",
    "riser": "Riser / one-line",
    "panel_schedule": "Panel schedules",
    "fixture_schedule": "Fixture schedule",
    "legend": "Legend / symbols",
    "notes": "Notes / general",
    "site": "Site",
    "details": "Details",
    "cover": "Cover / index",
    "unknown": "Unknown",
}
CLASS_STEPS = {
    "power_plan": ("symbol_detection", "scale"),
    "lighting_plan": ("symbol_detection", "scale"),
    "systems_plan": ("symbol_detection", "scale"),
    "site": ("symbol_detection", "scale"),
    "riser": ("riser", "scale"),
    "panel_schedule": ("panel_schedule", "fixture_schedule", "scale"),
    "fixture_schedule": ("fixture_schedule", "panel_schedule", "scale"),
    "legend": ("legend",),
    "notes": ("notes", "scale"),
    "details": ("scale",),
    "cover": (),
    "unknown": (),
}
STEP_LABELS = {
    "symbol_detection": "Symbol detection",
    "legend": "Legend",
    "fixture_schedule": "Fixture schedule",
    "panel_schedule": "Panel schedule",
    "notes": "Notes",
    "riser": "Riser",
    "scale": "Scale",
}
STEP_ORDER = tuple(STEP_LABELS)
PREFIX_CLASS = {
    "EP": "power_plan",
    "PW": "power_plan",
    "PWR": "power_plan",
    "EL": "lighting_plan",
    "LTG": "lighting_plan",
    "FA": "systems_plan",
    "LV": "systems_plan",
    "AV": "systems_plan",
    "SEC": "systems_plan",
    "OL": "riser",
    "PNL": "panel_schedule",
    "DET": "details",
    "GN": "notes",
    "CV": "cover",
    "SP": "site",
}
_CUE_STEP = {
    "power_plan": "symbol_detection",
    "lighting_plan": "symbol_detection",
    "systems_plan": "symbol_detection",
    "site": "symbol_detection",
    "riser": "riser",
    "panel_schedule": "panel_schedule",
    "fixture_schedule": "fixture_schedule",
    "legend": "legend",
    "notes": "notes",
    "details": "scale",
    "cover": "",
    "unknown": "",
}


class SheetError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def sheets_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "sheets" / f"{job_id}.json"


def load_overrides(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    raw = data.get("overrides") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    overrides = {}
    for key, value in raw.items():
        sheet_class = str(value or "").strip()
        if sheet_class in CLASS_LABELS and sheet_class != "unknown":
            overrides[str(key)] = sheet_class
    return overrides


def save_overrides(path: Path, overrides: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"overrides": {key: overrides[key] for key in sorted(overrides, key=_override_key)}}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def set_override(path: Path, page_index: int, sheet_class: str, page_count: int) -> None:
    if page_index < 0 or page_index >= page_count:
        raise SheetError("That sheet is not in this set.", status_code=404)
    overrides = load_overrides(path)
    key = str(page_index)
    if not sheet_class:
        overrides.pop(key, None)
    elif sheet_class not in CLASS_LABELS or sheet_class == "unknown":
        raise SheetError("Choose a sheet class.")
    else:
        overrides[key] = sheet_class
    save_overrides(path, overrides)


def classify_pages(pages: list[PageData], overrides: dict | None = None) -> list[dict]:
    saved = overrides or {}
    rows = []
    for page in pages:
        detected = classify_page(page)
        override = saved.get(str(page.index)) or ""
        if override:
            detected = _apply_override(detected, override)
        rows.append(detected)
    return rows


def pages_for_steps(pages: list[PageData], steps: tuple[str, ...] | list[str], overrides: dict | None = None) -> list[PageData]:
    wanted = set(steps)
    records = {row["page_index"]: row for row in classify_pages(pages, overrides)}
    chosen = []
    for page in pages:
        record = records.get(page.index) or {}
        if wanted.intersection(record.get("steps") or ()):
            chosen.append(page)
    return chosen


def classify_page(page: PageData) -> dict:
    """One class, its reasons, and a confidence of high, medium, or low."""

    height = float(page.height or 1)
    low_ocr = [word for word in page.words if is_low_ocr(word)]
    usable = [word for word in page.words if not is_low_ocr(word)]
    title_words = [word for word in usable if _in_title_block(word, height)]
    body_words = [word for word in usable if word not in title_words]
    title_text = _line_text(title_words)
    body_text = _line_text(body_words)
    title_scores, title_reasons = _cues(title_text, "Title")
    body_scores, body_reasons = _cues(body_text, "Body")
    scores = {key: title_scores.get(key, 0) + body_scores.get(key, 0) for key in SHEET_CLASSES if key != "unknown"}
    reasons = _merge_reasons(title_reasons, body_reasons)
    prefix = _prefix(page.sheet)
    prefix_class = PREFIX_CLASS.get(prefix, "")
    if prefix_class:
        reasons.setdefault(prefix_class, []).append(f"Sheet prefix {prefix} supports {CLASS_LABELS[prefix_class]}.")
        scores[prefix_class] = scores.get(prefix_class, 0) + 1
    elif prefix:
        reasons.setdefault("unknown", []).append(f"Sheet prefix {prefix} does not choose a class.")
    sheet_class, confidence = _choose(title_scores, scores, prefix_class)
    if sheet_class == "unknown" and prefix_class:
        sheet_class = prefix_class
        confidence = "medium"
    if int(getattr(page, "vertical_lines", 0) or 0) >= 4:
        reasons.setdefault(sheet_class, []).append("Column grid on the sheet.")
    if _dense_table(usable):
        reasons.setdefault(sheet_class, []).append("Words line up in a table.")
    if low_ocr and not title_words and not body_words:
        reasons.setdefault(sheet_class, []).append("Low-confidence OCR in the title block was not applied.")
    elif low_ocr:
        reasons.setdefault(sheet_class, []).append("Low-confidence OCR was not applied.")
    if any(word.source == "ocr" for word in title_words):
        reasons.setdefault(sheet_class, []).append("Title words came from OCR.")
    if sheet_class == "unknown" and "unknown" not in reasons:
        reasons["unknown"] = ["No title or layout cue."]
    steps = _steps(sheet_class, scores)
    ordered_reasons = _ordered_reasons(sheet_class, reasons, scores)
    return _record(page, sheet_class, confidence, ordered_reasons, steps, "")


def _apply_override(detected: dict, override: str) -> dict:
    reasons = ["Estimator override.", f"Detected class was {detected['label']}."]
    reasons.extend(reason for reason in detected["reasons"] if reason not in reasons)
    return {
        "page_index": detected["page_index"],
        "sheet": detected["sheet"],
        "title": detected["title"],
        "sheet_class": override,
        "label": CLASS_LABELS[override],
        "confidence": "high",
        "reasons": reasons,
        "text_source": detected["text_source"],
        "steps": list(CLASS_STEPS[override]),
        "step_labels": [STEP_LABELS[step] for step in CLASS_STEPS[override]],
        "override": override,
        "detected_class": detected["sheet_class"],
        "detected_label": detected["label"],
    }


def _record(page: PageData, sheet_class: str, confidence: str, reasons: list[str], steps: list[str], override: str) -> dict:
    return {
        "page_index": page.index,
        "sheet": page.sheet,
        "title": page.sheet_title or "",
        "sheet_class": sheet_class,
        "label": CLASS_LABELS[sheet_class],
        "confidence": confidence,
        "reasons": reasons,
        "text_source": page.text_source or "pdf",
        "steps": steps,
        "step_labels": [STEP_LABELS[step] for step in steps],
        "override": override,
        "detected_class": sheet_class,
        "detected_label": CLASS_LABELS[sheet_class],
    }


def _steps(sheet_class: str, scores: dict[str, int]) -> list[str]:
    found = list(CLASS_STEPS.get(sheet_class, ()))
    for key, score in scores.items():
        step = _CUE_STEP.get(key) or ""
        if score >= 4 and step and step not in found:
            found.append(step)
    return [step for step in STEP_ORDER if step in found]


def _choose(title_scores: dict[str, int], scores: dict[str, int], prefix_class: str) -> tuple[str, str]:
    title_best = _best(title_scores)
    if title_best and title_scores[title_best] >= 3 and _lead(title_scores, title_best) > 0:
        lead = _lead(title_scores, title_best)
        if title_scores[title_best] >= 4 and lead >= 2:
            return title_best, "high"
        return title_best, "medium"
    if title_best and title_scores[title_best] >= 4 and _lead(title_scores, title_best) == 0:
        return title_best, "low"
    body_best = _best(scores)
    if body_best and scores[body_best] >= 4 and _lead(scores, body_best) > 0:
        return body_best, "medium"
    if prefix_class:
        return prefix_class, "medium"
    return "unknown", "low"


def _best(scores: dict[str, int]) -> str:
    ranked = [key for key in SHEET_CLASSES if scores.get(key, 0) > 0]
    if not ranked:
        return ""
    top = max(scores[key] for key in ranked)
    tied = [key for key in SHEET_CLASSES if scores.get(key, 0) == top and key != "unknown"]
    return tied[0] if tied else ""


def _lead(scores: dict[str, int], winner: str) -> int:
    others = [scores.get(key, 0) for key in scores if key != winner]
    second = max(others, default=0)
    return scores.get(winner, 0) - second


def _cues(text: str, where: str) -> tuple[dict[str, int], dict[str, list[str]]]:
    folded = " ".join(text.upper().split())
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    def add(sheet_class: str, points: int, reason: str) -> None:
        scores[sheet_class] = scores.get(sheet_class, 0) + points
        reasons.setdefault(sheet_class, []).append(f"{where}: {reason}")

    if not folded:
        return scores, reasons
    if _any(folded, ("COVER SHEET", "DRAWING INDEX", "SHEET INDEX", "TITLE SHEET")):
        add("cover", 5, "cover or index wording.")
    if _any(folded, ("SYMBOL LEGEND", "SYMBOL SCHEDULE")) or re.search(r"\bLEGEND\b", folded):
        add("legend", 5, "legend wording.")
    fixture = _any(folded, ("LIGHTING FIXTURE SCHEDULE", "LUMINAIRE SCHEDULE", "FIXTURE SCHEDULE")) or (
        re.search(r"\bFIXTURES?\b", folded) and re.search(r"\bSCHEDULES?\b", folded)
    )
    panel = _any(folded, ("PANELBOARD SCHEDULE", "PANEL SCHEDULE")) or (
        re.search(r"\bPANELS?\b", folded) and re.search(r"\bSCHEDULES?\b", folded)
    )
    if fixture:
        add("fixture_schedule", 5, "fixture schedule wording.")
    if panel:
        add("panel_schedule", 5, "panel schedule wording.")
    if _any(folded, ("ONE-LINE", "ONE LINE", "SINGLE-LINE", "SINGLE LINE")) or re.search(r"\bRISERS?\b", folded):
        add("riser", 4, "riser or one-line wording.")
    if _any(folded, ("GENERAL NOTES", "GENERAL ELECTRICAL NOTES", "ELECTRICAL NOTES", "SHEET NOTES", "KEYNOTES", "KEYNOTE")):
        add("notes", 4, "notes wording.")
    if re.search(r"\bSITE PLAN\b", folded) or (re.search(r"\bSITE\b", folded) and re.search(r"\bPLAN\b", folded)):
        add("site", 5, "site plan wording.")
    elif re.search(r"\bSITE\b", folded):
        add("site", 3, "site wording.")
    if re.search(r"\bDETAILS?\b", folded):
        add("details", 4, "detail wording.")
    if _any(folded, ("FIRE ALARM", "LOW VOLTAGE", "SYSTEMS PLAN", "SECURITY PLAN", "DATA PLAN", "LIGHTING CONTROL")):
        add("systems_plan", 4, "systems or low-voltage wording.")
    if not fixture and (re.search(r"\bLIGHTING PLAN\b", folded) or re.search(r"\bLIGHTING\b", folded)):
        add("lighting_plan", 4, "lighting wording.")
    if re.search(r"\bPOWER\b", folded):
        add("power_plan", 4, "power wording.")
    return scores, reasons


def _ordered_reasons(sheet_class: str, reasons: dict[str, list[str]], scores: dict[str, int]) -> list[str]:
    ordered: list[str] = []
    keys = [sheet_class] + [key for key in SHEET_CLASSES if key != sheet_class and scores.get(key, 0) > 0]
    if sheet_class == "unknown":
        keys = ["unknown"] + [key for key in keys if key != "unknown"]
    for key in keys:
        for reason in reasons.get(key, []):
            if reason not in ordered:
                ordered.append(reason)
    for reason in reasons.get("unknown", []):
        if reason not in ordered:
            ordered.append(reason)
    return ordered


def _merge_reasons(*groups: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for group in groups:
        for key, items in group.items():
            for item in items:
                bucket = merged.setdefault(key, [])
                if item not in bucket:
                    bucket.append(item)
    return merged


def _any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}S?\b", text) for phrase in phrases)


def _prefix(sheet: str) -> str:
    token = re.sub(r"[^A-Z0-9]", "", (sheet or "").upper())
    match = re.match(r"[A-Z]+", token)
    if not match:
        return ""
    return match.group(0)


def _in_title_block(word: Word, height: float) -> bool:
    return word.cy < height * 0.18 or word.cy > height * 0.82


def _line_text(words: list[Word]) -> str:
    lines = group_lines(words)
    return " ".join(" ".join(word.text for word in line) for line in lines)


def _dense_table(words: list[Word]) -> bool:
    lines = [line for line in group_lines(words) if len(line) >= 3]
    if len(lines) < 4:
        return False
    columns: dict[int, set[int]] = {}
    for line in lines:
        for word in line:
            key = int(round(word.x0 / 8.0) * 8)
            columns.setdefault(key, set()).add(int(round(word.y0)))
    shared = [key for key, rows in columns.items() if len(rows) >= 4]
    return len(shared) >= 3


def _override_key(key: str) -> tuple:
    return (0, int(key)) if str(key).isdigit() else (1, str(key))
