"""Compare an estimator's quantities with the engine's counted lines.

Quantities arrive as a CSV or Excel sheet. The estimator maps the item,
description, quantity, unit, system, and optional sheet columns. A
submitted proposal is a CSV of line, description, qty, and unit price.

A line pairs by tag, then by normalized description, then by token
overlap. Overlap is automatic only when one engine line scores
0.90 or higher. Anything lower stays unpaired until the estimator
confirms it. Nothing here reads a vendor price book or routes conduit.
"""

from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook

from adel_takeoff.assemblies import data_dir_for_job


FUZZY_AUTO_THRESHOLD = Decimal("0.90")
FUZZY_SUGGEST_THRESHOLD = Decimal("0.60")

QUANTITY_FIELDS = (
    ("tag", "Item / tag", True),
    ("description", "Description", True),
    ("quantity", "Quantity", True),
    ("unit", "Unit", True),
    ("system", "System", True),
    ("sheet", "Sheet", False),
)
PROPOSAL_FIELDS = (
    ("tag", "Line", True),
    ("description", "Description", True),
    ("quantity", "Quantity", True),
    ("unit_price", "Unit price", True),
)
PROPOSAL_ALIASES = {
    "tag": {"line", "item", "tag", "mark"},
    "description": {"description", "desc"},
    "quantity": {"qty", "quantity"},
    "unit_price": {"unit price", "unitprice", "price"},
}
STATUSES = (
    "match",
    "engine higher",
    "estimator higher",
    "only in estimator",
    "only in engine",
)


class EstimateError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def estimate_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "estimates" / f"{job_id}.json"


def load_estimate(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _stored(data)


def save_estimate(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _stored(store)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_upload(kind: str, filename: str, payload: bytes) -> dict:
    if kind not in {"quantities", "proposal"}:
        raise EstimateError("Choose estimator quantities or a proposal.")
    headers, rows = _read_table(filename or "upload", payload, kind)
    mapping = _auto_proposal_mapping(headers) if kind == "proposal" else None
    store = _stored(
        {
            "kind": kind,
            "source_name": Path(filename or "upload").name,
            "headers": headers,
            "rows": rows,
            "mapping": mapping,
            "pairs": {},
        }
    )
    if mapping is not None:
        _parsed_rows(store)
    return store


def apply_mapping(store: dict, submitted: dict) -> dict:
    if not store or not store.get("headers"):
        raise EstimateError("Import a file before mapping columns.")
    mapping = _clean_mapping(store, submitted)
    updated = _stored({**store, "mapping": mapping})
    _parsed_rows(updated)
    return updated


def set_pair(store: dict, estimator_id: str, engine_line_id: str, engine_ids: set[str]) -> dict:
    if not store or not store.get("mapping"):
        raise EstimateError("Map the columns before pairing a line.")
    known = {row["id"] for row in _parsed_rows(store)}
    if estimator_id not in known:
        raise EstimateError("That estimator line is not in the import.")
    if engine_line_id not in engine_ids:
        raise EstimateError("That engine line is not in the takeoff.")
    pairs = dict(store.get("pairs") or {})
    for other, target in pairs.items():
        if other != estimator_id and target == engine_line_id:
            raise EstimateError("That engine line is already paired.")
    pairs[estimator_id] = engine_line_id
    return _stored({**store, "pairs": pairs})


def clear_pair(store: dict, estimator_id: str) -> dict:
    if not store:
        raise EstimateError("Import a file before clearing a pair.")
    pairs = dict(store.get("pairs") or {})
    pairs.pop(estimator_id, None)
    return _stored({**store, "pairs": pairs})


def comparison_for_job(job: dict, lines: list[dict]) -> dict:
    data_dir = data_dir_for_job(job)
    store = None
    if data_dir is not None and job.get("id"):
        store = load_estimate(estimate_path(data_dir, str(job["id"])))
    return compare_estimate(store, lines)


def compare_estimate(store: dict | None, lines: list[dict]) -> dict:
    engines = _engine_views(lines)
    base = {
        "present": bool(store and store.get("headers")),
        "mapped": bool(store and store.get("mapping")),
        "kind": (store or {}).get("kind") or "",
        "source_name": (store or {}).get("source_name") or "",
        "headers": list((store or {}).get("headers") or []),
        "header_labels": _header_labels((store or {}).get("headers") or []),
        "preview": [row["cells"] for row in ((store or {}).get("rows") or [])[:8]],
        "fields": _field_state(store),
        "rows": [],
        "engine_choices": [],
        "firm_price_delta": "0.00",
        "review_price_delta": "0.00",
        "threshold": format(FUZZY_AUTO_THRESHOLD, "f"),
        "suggest_threshold": format(FUZZY_SUGGEST_THRESHOLD, "f"),
        "error": "",
    }
    if not store or not store.get("mapping"):
        base["engine_choices"] = [_choice(engine, "") for engine in engines]
        return base
    try:
        parsed = _parsed_rows(store)
    except EstimateError as exc:
        base["error"] = str(exc)
        base["engine_choices"] = [_choice(engine, "") for engine in engines]
        return base
    rows = _match(parsed, engines, dict(store.get("pairs") or {}))
    firm = Decimal("0.00")
    review = Decimal("0.00")
    held: dict[str, str] = {}
    for row in rows:
        if row["engine_line_id"] and row["estimator_id"]:
            held[row["engine_line_id"]] = row["estimator_id"]
        if row["price_delta"] == "":
            continue
        amount = Decimal(row["price_delta"])
        if row["review"] == "firm":
            firm += amount
        else:
            review += amount
    base["rows"] = rows
    base["firm_price_delta"] = _money(firm)
    base["review_price_delta"] = _money(review)
    base["engine_choices"] = [_choice(engine, held.get(engine["line_id"], "")) for engine in engines]
    return base


def base_engine_ids(lines: list[dict]) -> set[str]:
    return {engine["line_id"] for engine in _engine_views(lines)}


def _stored(store: dict) -> dict:
    mapping = store.get("mapping")
    clean_mapping = None
    if isinstance(mapping, dict):
        clean_mapping = {}
        for key, value in mapping.items():
            if value is None or value == "":
                clean_mapping[str(key)] = None
            else:
                clean_mapping[str(key)] = int(value)
    rows = []
    for item in store.get("rows") or []:
        if not isinstance(item, dict):
            continue
        cells = ["" if cell is None else str(cell) for cell in item.get("cells") or []]
        try:
            number = int(item.get("n") or 0)
        except (TypeError, ValueError):
            number = 0
        rows.append({"n": number, "cells": cells})
    pairs = {}
    raw_pairs = store.get("pairs") or {}
    if isinstance(raw_pairs, dict):
        for key, value in raw_pairs.items():
            if key and value:
                pairs[str(key)] = str(value)
    return {
        "kind": store.get("kind") if store.get("kind") in {"quantities", "proposal"} else "",
        "source_name": str(store.get("source_name") or ""),
        "headers": [str(cell) for cell in (store.get("headers") or [])],
        "rows": rows,
        "mapping": clean_mapping,
        "pairs": pairs,
    }


def _read_table(filename: str, payload: bytes, kind: str) -> tuple[list[str], list[dict]]:
    suffix = Path(filename).suffix.lower()
    if kind == "proposal":
        if suffix != ".csv":
            raise EstimateError(
                "A proposal import is a CSV of line, description, qty, and unit price."
            )
        matrix = _read_csv(payload)
    elif suffix == ".csv":
        matrix = _read_csv(payload)
    elif suffix == ".xlsx":
        matrix = _read_xlsx(payload)
    else:
        raise EstimateError("Use a CSV or Excel file for the estimator quantities.")
    return _header_and_rows(matrix)


def _read_csv(payload: bytes) -> list[list[str]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EstimateError("The CSV is not UTF-8 text.") from exc
    return [[_text(cell) for cell in row] for row in csv.reader(io.StringIO(text))]


def _read_xlsx(payload: bytes) -> list[list[str]]:
    try:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    except Exception as exc:
        raise EstimateError("The Excel file could not be read.") from exc
    try:
        sheet = workbook.worksheets[0]
        return [[_text(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _header_and_rows(matrix: list[list[str]]) -> tuple[list[str], list[dict]]:
    start = 0
    while start < len(matrix) and not any(cell.strip() for cell in matrix[start]):
        start += 1
    if start >= len(matrix):
        raise EstimateError("The file has no header row.")
    header = list(matrix[start])
    while header and not header[-1].strip():
        header.pop()
    if not header or not any(cell.strip() for cell in header):
        raise EstimateError("The file has no header row.")
    width = len(header)
    rows = []
    for offset, raw in enumerate(matrix[start + 1 :], start=start + 2):
        cells = list(raw[:width]) + [""] * max(0, width - len(raw))
        cells = cells[:width]
        if not any(cell.strip() for cell in cells):
            continue
        rows.append({"n": offset, "cells": cells})
    return header, rows


def _auto_proposal_mapping(headers: list[str]) -> dict | None:
    keys = [_header_key(cell) for cell in headers]
    mapping: dict[str, int] = {}
    for field, aliases in PROPOSAL_ALIASES.items():
        hits = [index for index, key in enumerate(keys) if key in aliases]
        if len(hits) != 1:
            return None
        mapping[field] = hits[0]
    if len(set(mapping.values())) != len(mapping):
        return None
    return mapping


def _clean_mapping(store: dict, submitted: dict) -> dict:
    width = len(store.get("headers") or [])
    specs = PROPOSAL_FIELDS if store.get("kind") == "proposal" else QUANTITY_FIELDS
    chosen: dict[str, int | None] = {}
    used: list[int] = []
    for key, label, required in specs:
        raw = str(submitted.get(key) or "").strip()
        if raw == "":
            if required:
                raise EstimateError(f"Map a column for {label.lower()}.")
            chosen[key] = None
            continue
        if not raw.isdigit():
            raise EstimateError(f"Map a column for {label.lower()}.")
        index = int(raw)
        if index < 0 or index >= width:
            raise EstimateError("That column is not in the file.")
        if index in used:
            raise EstimateError("Each column can be used once.")
        used.append(index)
        chosen[key] = index
    return chosen


def _parsed_rows(store: dict) -> list[dict]:
    mapping = store.get("mapping") or {}
    kind = store.get("kind")
    parsed = []
    sequence = 0
    for raw in store.get("rows") or []:
        cells = raw["cells"]
        picked = {key: _at(cells, mapping.get(key)) for key in mapping}
        if not any(picked.values()):
            continue
        sequence += 1
        quantity = _required_number(picked.get("quantity") or "", raw["n"], "quantity")
        unit_price = None
        if kind == "proposal":
            unit_price = _required_number(picked.get("unit_price") or "", raw["n"], "unit price")
        parsed.append(
            {
                "id": f"e{sequence}",
                "tag": picked.get("tag") or "",
                "description": picked.get("description") or "",
                "quantity": quantity,
                "unit": (picked.get("unit") or "").strip() or ("EA" if kind == "proposal" else ""),
                "system": (picked.get("system") or "").strip(),
                "sheet": (picked.get("sheet") or "").strip(),
                "unit_price": unit_price,
                "source_row": raw["n"],
            }
        )
    return parsed


def _required_number(text: str, row_number: int, label: str) -> Decimal:
    cleaned = text.strip().replace(",", "").replace("$", "").replace(" ", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if not cleaned:
        raise EstimateError(f"Row {row_number} {label} is not a number.")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise EstimateError(f"Row {row_number} {label} is not a number.") from exc


def _engine_views(lines: list[dict]) -> list[dict]:
    views = []
    for line in lines:
        if not line.get("counts_in_base", True):
            continue
        if line.get("effective") is None:
            continue
        views.append(
            {
                "line_id": str(line.get("line_id") or ""),
                "label": str(line.get("label") or ""),
                "type_mark": str(line.get("type_mark") or ""),
                "type_description": str(line.get("type_description") or ""),
                "quantity": Decimal(str(line["effective"])),
                "sheets": str(line.get("sheet_number") or ""),
                "system": str(line.get("category") or ""),
            }
        )
    views.sort(key=lambda item: item["line_id"])
    return [item for item in views if item["line_id"]]


def _match(parsed: list[dict], engines: list[dict], pairs: dict[str, str]) -> list[dict]:
    by_id = {engine["line_id"]: engine for engine in engines}
    assigned: dict[str, dict] = {}
    used: set[str] = set()

    for row in parsed:
        target = pairs.get(row["id"])
        if not target or target not in by_id or target in used:
            continue
        assigned[row["id"]] = _linked(row, by_id[target], "manual", "firm", None, [])
        used.add(target)

    for row in parsed:
        if row["id"] in assigned:
            continue
        hits = [engine for engine in engines if engine["line_id"] not in used and _tag_hit(row, engine)]
        hits.sort(key=lambda engine: engine["line_id"])
        if len(hits) == 1:
            assigned[row["id"]] = _linked(row, hits[0], "tag", "firm", None, [])
            used.add(hits[0]["line_id"])
        elif len(hits) > 1:
            assigned[row["id"]] = _open(row, "ambiguous", hits)

    for row in parsed:
        if row["id"] in assigned:
            continue
        hits = [
            engine
            for engine in engines
            if engine["line_id"] not in used and _description_hit(row, engine)
        ]
        hits.sort(key=lambda engine: engine["line_id"])
        if len(hits) == 1:
            assigned[row["id"]] = _linked(row, hits[0], "description", "firm", None, [])
            used.add(hits[0]["line_id"])
        elif len(hits) > 1:
            assigned[row["id"]] = _open(row, "ambiguous", hits)

    for row in parsed:
        if row["id"] in assigned:
            continue
        scored = []
        for engine in engines:
            if engine["line_id"] in used:
                continue
            score = _jaccard(_estimator_tokens(row), _engine_tokens(engine))
            if score >= FUZZY_SUGGEST_THRESHOLD:
                scored.append((score, engine))
        scored.sort(key=lambda item: (-item[0], item[1]["line_id"]))
        if not scored:
            continue
        best = scored[0][0]
        top = [item for item in scored if item[0] == best]
        if len(top) == 1 and best >= FUZZY_AUTO_THRESHOLD:
            score, engine = top[0]
            assigned[row["id"]] = _linked(row, engine, "fuzzy", "firm", score, [])
            used.add(engine["line_id"])
        elif len(top) == 1:
            score, engine = top[0]
            assigned[row["id"]] = _linked(row, engine, "suggestion", "needs-review", score, [])
            used.add(engine["line_id"])
        else:
            assigned[row["id"]] = _open(
                row,
                "ambiguous",
                [engine for _score, engine in top],
                scores={engine["line_id"]: score for score, engine in top},
            )

    rows = []
    for row in parsed:
        if row["id"] in assigned:
            rows.append(assigned[row["id"]])
        else:
            rows.append(_open(row, "only", []))
    for engine in engines:
        if engine["line_id"] not in used:
            rows.append(_engine_only(engine))
    return _ordered(rows)


def _linked(row, engine, method: str, review: str, score: Decimal | None, candidates: list) -> dict:
    return _comparison_row(
        estimator=row,
        engine=engine,
        method=method,
        review=review,
        score=score,
        candidates=candidates,
    )


def _open(row, method: str, candidates: list[dict], scores: dict | None = None) -> dict:
    listed = []
    for engine in candidates:
        listed.append(
            {
                "line_id": engine["line_id"],
                "label": engine["label"],
                "quantity": _qty(engine["quantity"]),
                "sheets": engine["sheets"],
                "score": _score_text((scores or {}).get(engine["line_id"])),
            }
        )
    review = "needs-review" if candidates else "firm"
    return _comparison_row(
        estimator=row,
        engine=None,
        method=method if candidates else "only",
        review=review,
        score=None,
        candidates=listed,
    )


def _engine_only(engine: dict) -> dict:
    return _comparison_row(
        estimator=None,
        engine=engine,
        method="only",
        review="firm",
        score=None,
        candidates=[],
    )


def _comparison_row(estimator, engine, method: str, review: str, score, candidates: list) -> dict:
    estimator_qty = estimator["quantity"] if estimator else None
    engine_qty = engine["quantity"] if engine else None
    delta = (engine_qty or Decimal("0")) - (estimator_qty or Decimal("0"))
    unit_price = estimator["unit_price"] if estimator else None
    price_delta = None if unit_price is None else delta * unit_price
    status = _status(estimator is not None, engine is not None, estimator_qty, engine_qty)
    system = ""
    if estimator and estimator.get("system"):
        system = estimator["system"]
    elif engine:
        system = engine["system"]
    return {
        "estimator_id": estimator["id"] if estimator else "",
        "engine_line_id": engine["line_id"] if engine else "",
        "tag": (estimator["tag"] if estimator else "") or (engine["label"] if engine else ""),
        "description": (estimator["description"] if estimator else "")
        or (engine["type_description"] if engine else ""),
        "estimator_qty": "" if estimator_qty is None else _qty(estimator_qty),
        "engine_qty": "" if engine_qty is None else _qty(engine_qty),
        "unit": (estimator["unit"] if estimator else "") or "",
        "unit_price": "" if unit_price is None else _money(unit_price),
        "delta": _qty(delta),
        "price_delta": "" if price_delta is None else _money(price_delta),
        "status": status,
        "review": review,
        "sheets": engine["sheets"] if engine else "",
        "estimator_sheet": estimator["sheet"] if estimator else "",
        "system": system,
        "method": method,
        "score": _score_text(score),
        "candidates": candidates,
    }


def _ordered(rows: list[dict]) -> list[dict]:
    paired = [row for row in rows if row["estimator_id"] and row["engine_line_id"]]
    estimator_only = [row for row in rows if row["estimator_id"] and not row["engine_line_id"]]
    engine_only = [row for row in rows if row["engine_line_id"] and not row["estimator_id"]]
    paired.sort(key=lambda row: (_estimator_key(row["estimator_id"]), row["engine_line_id"]))
    estimator_only.sort(key=lambda row: _estimator_key(row["estimator_id"]))
    engine_only.sort(key=lambda row: row["engine_line_id"])
    return paired + estimator_only + engine_only


def _status(has_estimator: bool, has_engine: bool, estimator_qty, engine_qty) -> str:
    if has_estimator and not has_engine:
        return "only in estimator"
    if has_engine and not has_estimator:
        return "only in engine"
    left = estimator_qty or Decimal("0")
    right = engine_qty or Decimal("0")
    if right == left:
        return "match"
    if right > left:
        return "engine higher"
    return "estimator higher"


def _tag_hit(row: dict, engine: dict) -> bool:
    tag = _norm(row.get("tag") or "")
    if not tag:
        return False
    keys = {_norm(engine["label"]), _norm(engine["type_mark"])}
    keys.discard("")
    return tag in keys


def _description_hit(row: dict, engine: dict) -> bool:
    description = _norm(row.get("description") or "")
    if not description:
        return False
    keys = {_norm(engine["label"]), _norm(engine["type_description"])}
    keys.discard("")
    return description in keys


def _estimator_tokens(row: dict) -> frozenset[str]:
    return _tokens(f"{row.get('tag') or ''} {row.get('description') or ''}")


def _engine_tokens(engine: dict) -> frozenset[str]:
    return _tokens(
        " ".join(
            [
                engine.get("label") or "",
                engine.get("type_mark") or "",
                engine.get("type_description") or "",
            ]
        )
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> Decimal:
    union = len(left | right)
    if union == 0:
        return Decimal("0")
    return Decimal(len(left & right)) / Decimal(union)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).split())


def _header_key(text: str) -> str:
    return _norm(text)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in _norm(text).split() if token)


def _header_labels(headers: list[str]) -> list[str]:
    return [cell.strip() or f"Column {index + 1}" for index, cell in enumerate(headers)]


def _field_state(store: dict | None) -> list[dict]:
    if not store:
        return []
    specs = PROPOSAL_FIELDS if store.get("kind") == "proposal" else QUANTITY_FIELDS
    mapping = store.get("mapping") or {}
    return [
        {
            "key": key,
            "label": label,
            "required": required,
            "selected": mapping.get(key),
        }
        for key, label, required in specs
    ]


def _choice(engine: dict, paired_to: str) -> dict:
    return {
        "line_id": engine["line_id"],
        "label": engine["label"],
        "quantity": _qty(engine["quantity"]),
        "sheets": engine["sheets"],
        "paired_to": paired_to,
    }


def _at(cells: list[str], index) -> str:
    if index is None:
        return ""
    try:
        position = int(index)
    except (TypeError, ValueError):
        return ""
    if position < 0 or position >= len(cells):
        return ""
    return cells[position].strip()


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(Decimal(str(value)), "f").rstrip("0").rstrip(".") or "0"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def _qty(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _score_text(score: Decimal | None) -> str:
    if score is None:
        return ""
    return format(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _estimator_key(identifier: str) -> tuple:
    match = re.fullmatch(r"e(\d+)", identifier or "")
    if match:
        return (int(match.group(1)), identifier)
    return (10**9, identifier or "")
