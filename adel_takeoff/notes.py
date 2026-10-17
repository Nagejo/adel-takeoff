"""Numbered general notes and keynotes from sheet text.

A note is a numbered line under a general-notes or keynotes heading. The
heading and the number have to be printed. A sentence that merely says NOTE
is left alone. PDF text and OCR words use the same parser. Low-confidence
OCR is kept and marked for check. It does not invent a note the sheet did
not print.
"""

from __future__ import annotations

import re

from adel_takeoff.extract import PageData, group_lines, is_low_ocr


KEYNOTE_ATTACH_PT = 72.0
DISPOSITIONS = (
    ("included", "Priced / included"),
    ("excluded", "Excluded"),
    ("clarification", "Clarification"),
    ("alternate_add", "Alternate, add"),
    ("alternate_deduct", "Alternate, deduct"),
    ("rfi", "RFI"),
    ("not_applicable", "Not applicable"),
)
DISPOSITION_VALUES = {key for key, _label in DISPOSITIONS}

_GENERAL = (
    "GENERAL ELECTRICAL NOTES",
    "ELECTRICAL GENERAL NOTES",
    "GENERAL NOTES",
    "ELECTRICAL NOTES",
    "SHEET NOTES",
)
_KEYNOTE = (
    "KEYNOTE LEGEND",
    "KEYNOTE SCHEDULE",
    "KEYNOTES",
    "KEY NOTES",
)
_NUMBERED = re.compile(r"^(?P<num>\d{1,3})[.)]?\s+(?P<body>.+)$")
_TAG = re.compile(
    r"\b(?:KEYNOTE|KEY\s*NOTE|KN|K\.N\.)\s*[-:]?\s*(?P<num>\d{1,3})\b",
    re.IGNORECASE,
)
_BLOCK_GAP = 36.0
_CONTINUE_GAP = 18.0


def disposition_label(value: str) -> str:
    for key, label in DISPOSITIONS:
        if key == value:
            return label
    return ""


def parse_notes(pages: list[PageData]) -> list[dict]:
    """Return numbered notes in reading order."""

    lines = _lines(pages)
    notes: list[dict] = []
    consumed: set[int] = set()
    index = 0
    while index < len(lines):
        kind = _heading(lines[index]["text"])
        if kind is None:
            index += 1
            continue
        block, index = _consume(lines, index, kind)
        for note in block:
            consumed.update(note.pop("_lines"))
            notes.append(note)
    _attach_tags(notes, lines, consumed)
    return notes


def link_keynotes(notes: list[dict], rows: list[dict]) -> None:
    """Attach each keynote tag to the nearest counted mark inside the radius."""

    for row in rows:
        row["keynotes"] = []
    for note in notes:
        note["item_ids"] = []
        note["linked"] = []
        if note["kind"] != "keynote":
            continue
        for tag in note.get("tags") or []:
            chosen = _nearest_row(rows, tag)
            if chosen is None:
                continue
            item_id = str(chosen["id"])
            if item_id in note["item_ids"]:
                continue
            note["item_ids"].append(item_id)
            note["linked"].append({"id": item_id, "label": chosen.get("label") or ""})
            chosen["keynotes"].append(
                {
                    "id": note["id"],
                    "number": note["number"],
                    "text": note["text"],
                    "x": tag["x"],
                    "y": tag["y"],
                }
            )


def attention_for(text: str) -> dict:
    """A keyword hint for notes that change cost. It is not a disposition."""

    folded = " ".join(text.casefold().split())
    rules = (
        (
            ("romex", "nm cable", "owner approval"),
            "Owner approval can change the wiring method. Price the base bid and carry a deduct alternate.",
            "Owner",
            "Base bid includes conduit. Romex is a deduct alternate if the owner approves it.",
        ),
        (
            ("telco", "catv", "empty pvc", "empty conduit"),
            "Empty raceway for another trade is a scope and cost item.",
            "Owner",
            "Include the empty PVC shown. Confirm the telco and CATV pathway is in this contract.",
        ),
        (
            ("field change", "no extra charge"),
            "A no-extra-charge note is a change-order risk and needs a qualification.",
            "GC",
            "Field changes are not included at no charge. Qualify the note.",
        ),
        (
            ("other trades", "shown on other"),
            "Connecting other trades' equipment can add devices that are not on this sheet.",
            "GC",
            "Connect only the equipment identified on the electrical drawings.",
        ),
        (
            ("warranty", "1-year", "one-year", "one year"),
            "A warranty term is a cost. Confirm the duration before it is included.",
            "Owner",
            "Include a 1-year warranty only when that term is printed.",
        ),
        (
            ("prewire", "low voltage", "low-voltage"),
            "Low-voltage prewire can conflict with the site plan or sit outside the EC scope.",
            "ID",
            "Confirm the prewire scope against the site plan before including it.",
        ),
    )
    for needles, why, who, assumption in rules:
        if any(needle in folded for needle in needles):
            return {"why": why, "who": who, "assumption": assumption}
    return {
        "why": "This numbered note can change scope or cost. Give it a disposition.",
        "who": "Engineer",
        "assumption": "",
    }


def _lines(pages: list[PageData]) -> list[dict]:
    found = []
    for page in pages:
        for words in group_lines(page.words):
            text = " ".join(word.text for word in words).strip()
            if not text:
                continue
            found.append(
                {
                    "text": text,
                    "page_index": page.index,
                    "sheet": page.sheet or f"Page {page.index + 1}",
                    "x": round(sum(word.cx for word in words) / len(words), 2),
                    "y": round(min(word.y0 for word in words), 2),
                    "source": "ocr" if any(word.source == "ocr" for word in words) else "pdf",
                    "ocr_check": any(is_low_ocr(word) for word in words),
                }
            )
    return found


def _heading(text: str) -> str | None:
    cleaned = re.sub(r"[:.\s]+$", "", " ".join(text.upper().split()))
    for phrase in _GENERAL:
        if cleaned == phrase:
            return "general"
    for phrase in _KEYNOTE:
        if cleaned == phrase:
            return "keynote"
    return None


def _consume(lines: list[dict], start: int, kind: str) -> tuple[list[dict], int]:
    heading = lines[start]
    notes: list[dict] = []
    current: dict | None = None
    last_y = heading["y"]
    seen: dict[str, int] = {}
    index = start + 1
    consumed = []
    while index < len(lines):
        line = lines[index]
        if line["page_index"] != heading["page_index"]:
            break
        if _heading(line["text"]) is not None:
            break
        if line["y"] - last_y > _BLOCK_GAP:
            break
        match = _NUMBERED.match(line["text"].strip())
        body = match.group("body").strip() if match else ""
        if match and re.search(r"[A-Za-z]", body):
            number = str(int(match.group("num")))
            seen[number] = seen.get(number, 0) + 1
            suffix = "" if seen[number] == 1 else f"-{seen[number]}"
            current = {
                "id": f"{heading['sheet']}:{kind}:{number}{suffix}",
                "kind": kind,
                "number": number,
                "text": body,
                "sheet": heading["sheet"],
                "page_index": heading["page_index"],
                "x": line["x"],
                "y": line["y"],
                "source": line["source"],
                "ocr_check": line["ocr_check"],
                "disposition": "",
                "tags": [],
                "item_ids": [],
                "linked": [],
                "_lines": [index],
            }
            current.update(attention_for(body))
            notes.append(current)
            consumed.append(index)
        elif current is not None and line["y"] - last_y <= _CONTINUE_GAP:
            current["text"] = f"{current['text']} {line['text'].strip()}".strip()
            current["ocr_check"] = current["ocr_check"] or line["ocr_check"]
            if line["source"] == "ocr":
                current["source"] = "ocr"
            current["_lines"].append(index)
            hint = attention_for(current["text"])
            current["why"] = hint["why"]
            current["who"] = hint["who"]
            current["assumption"] = hint["assumption"]
            consumed.append(index)
        else:
            break
        last_y = line["y"]
        index += 1
    for note in notes:
        note["_lines"] = [item for item in note["_lines"]]
    return notes, index


def _attach_tags(notes: list[dict], lines: list[dict], consumed: set[int]) -> None:
    by_number: dict[tuple[str, str], list[dict]] = {}
    for note in notes:
        if note["kind"] != "keynote":
            continue
        by_number.setdefault((note["sheet"], note["number"]), []).append(note)
        by_number.setdefault(("", note["number"]), []).append(note)
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        for match in _TAG.finditer(line["text"]):
            number = str(int(match.group("num")))
            pool = by_number.get((line["sheet"], number)) or by_number.get(("", number)) or []
            if not pool:
                continue
            pool[0]["tags"].append(
                {
                    "page_index": line["page_index"],
                    "sheet": line["sheet"],
                    "x": line["x"],
                    "y": line["y"],
                    "text": line["text"],
                }
            )


def _nearest_row(rows: list[dict], tag: dict) -> dict | None:
    best: tuple[float, str, dict] | None = None
    for row in rows:
        for occurrence in row.get("occurrences") or []:
            if int(occurrence.get("page_index") or 0) != int(tag["page_index"]):
                continue
            x0 = float(occurrence.get("x0") or 0)
            y0 = float(occurrence.get("y0") or 0)
            x1 = float(occurrence.get("x1") or x0)
            y1 = float(occurrence.get("y1") or y0)
            distance = ((tag["x"] - (x0 + x1) / 2) ** 2 + (tag["y"] - (y0 + y1) / 2) ** 2) ** 0.5
            if distance > KEYNOTE_ATTACH_PT:
                continue
            item_id = str(row.get("id") or "")
            if best is None or (round(distance, 4), item_id) < (round(best[0], 4), best[1]):
                best = (distance, item_id, row)
    if best is None:
        return None
    return best[2]
