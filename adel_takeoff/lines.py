"""Roll detected items into quantity lines and describe the arithmetic.

A line total is the sum of the per-sheet hits minus overlaps the stitch
already removed. The text names the method. It does not invent a count.
"""

from __future__ import annotations

from decimal import Decimal

from adel_takeoff.confidence import combine_confidence


METHOD_LABELS = {
    "legend_match": "legend match",
    "text_tag": "text tag",
    "manual_add": "manual add",
    "symbol_search": "example match",
    "corrected_type": "corrected type",
    "unmatched": "unmatched geometry",
}


def method_label(method: str) -> str:
    return METHOD_LABELS.get(method, method)


def _joined(members: list[dict], key: str) -> str:
    return "; ".join(_unique(row.get(key) or "" for row in members))


def _unique(values) -> list[str]:
    found: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in found:
            found.append(text)
    return found


def _counts_as_mark(row: dict) -> bool:
    methods = row.get("count_methods") or [row.get("count_method") or ""]
    return any(method != "unmatched" for method in methods)


def _arithmetic(members: list[dict]) -> str:
    if not any(_counts_as_mark(row) for row in members):
        return members[0].get("link_flag") or "unmatched geometry; not counted"

    labels: list[str] = []
    for row in members:
        methods = row.get("count_methods") or [row.get("count_method") or ""]
        for method in methods:
            label = method_label(method)
            if label and label not in labels:
                labels.append(label)
    method_text = ", ".join(labels) if labels else "counted"

    order: list[str] = []
    counts: dict[str, int] = {}
    item_ids: dict[str, list[str]] = {}
    overlaps = 0
    for row in members:
        if row.get("deduped"):
            overlaps += max(0, len(row.get("occurrences") or []) - 1)
        for occurrence in row.get("occurrences") or []:
            sheet = occurrence.get("sheet") or "sheet unknown"
            if sheet not in counts:
                order.append(sheet)
                counts[sheet] = 0
                item_ids[sheet] = []
            counts[sheet] += 1
            if row["id"] not in item_ids[sheet]:
                item_ids[sheet].append(row["id"])

    parts = []
    for sheet in order:
        ids = item_ids[sheet]
        noun = "item" if len(ids) == 1 else "items"
        parts.append(f"{sheet}: {counts[sheet]} ({noun} {', '.join(ids)})")
    bits = " + ".join(parts) if parts else "no sheet hits"
    measured_total = sum(counts.values()) - overlaps
    if overlaps == 1:
        bits += " - 1 overlap"
    elif overlaps > 1:
        bits += f" - {overlaps} overlaps"
    bits += f" = {measured_total}"
    text = f"{method_text}; {bits}"

    corrections = []
    for row in members:
        if row.get("override") is not None:
            corrections.append(
                f"item {row['id']} count correction {row['override']} "
                f"(measured {row['measured']})"
            )
    if corrections:
        text += "; " + "; ".join(corrections)
        effective_values = [
            row["effective"] for row in members if row.get("effective") is not None
        ]
        if effective_values and len(effective_values) == len(members):
            text += f"; total {sum(effective_values)}"
    return text


def how_counted(members: list[dict], excluded_reason: str = "") -> str:
    text = _arithmetic(members)
    if not excluded_reason:
        return text
    if excluded_reason in text:
        return f"{text}; not in the counted total"
    return f"{text}; excluded ({excluded_reason}); not in the counted total"


def exclusion_reason(row: dict) -> str:
    if row.get("review_status") == "rejected" or row.get("status") == "rejected":
        return "rejected"
    exclusion = row.get("exclusion") or ""
    if exclusion == "existing_to_remain":
        return "existing to remain"
    if exclusion == "nic":
        return "not in contract"
    if exclusion == "by_others" or row.get("status") == "by_others":
        return "by others"
    if row.get("status") == "unmatched":
        return row.get("link_flag") or "unmatched; not counted"
    if row.get("status") == "not_counted":
        return "not counted"
    if row.get("status") == "excluded":
        return exclusion or "excluded"
    return row.get("status") or "excluded"


def _same_number(values: list) -> object | None:
    present = [value for value in values if value is not None and value != ""]
    if len(present) != len(values) or not present:
        return None
    first = present[0]
    if all(value == first for value in present):
        return first
    return None


def _sum_decimal(values: list) -> Decimal | None:
    total = Decimal("0")
    any_value = False
    for value in values:
        if value is None:
            continue
        total += value
        any_value = True
    if not any_value:
        return None
    return total


def line_from_members(members: list[dict], *, excluded_reason: str = "") -> dict:
    ids = [row["id"] for row in members]
    line_id = ids[0] if len(ids) == 1 else "+".join(ids)
    sheets: list[str] = []
    titles: list[str] = []
    pages: list[str] = []
    for row in members:
        for sheet in row.get("sheet_numbers") or []:
            if sheet not in sheets:
                sheets.append(sheet)
        for title in row.get("sheet_titles") or []:
            if title not in titles:
                titles.append(title)
        for page in row.get("pdf_pages") or []:
            if page not in pages:
                pages.append(page)
    scores = [row.get("detector_score") for row in members]
    score_values = [score for score in scores if score is not None]
    detector_score = min(score_values) if score_values and len(score_values) == len(scores) else None
    flags = [row.get("confidence") or "" for row in members]
    rate_keys = {row.get("labor_rate_key") or "" for row in members}
    link_flags = _unique(row.get("link_flag") or "" for row in members)
    measured_values = [row.get("measured") for row in members]
    if len(members) == 1:
        measured = measured_values[0]
        override = members[0].get("override")
    else:
        measured = sum(value or 0 for value in measured_values)
        override = None
    effective_values = [row.get("effective") for row in members]
    if any(value is None for value in effective_values):
        effective = None
    else:
        effective = sum(effective_values)
    return {
        "line_id": line_id,
        "item_ids": ids,
        "label": members[0]["label"],
        "category": members[0]["category"],
        "assembly_id": members[0].get("assembly_id") or "",
        "assembly_name": members[0].get("assembly_name") or "",
        "assembly_version": members[0].get("assembly_version") or "",
        "assembly_confirmed": bool(members[0].get("assembly_confirmed")),
        "assembly_example": bool(members[0].get("assembly_example")),
        "assembly_hours_per_unit": _same_number(
            [row.get("assembly_hours_per_unit") for row in members]
        ),
        "assembly_labor_hours": _sum_decimal(
            [row.get("assembly_labor_hours") for row in members]
        ),
        "assembly_rate_key": members[0].get("assembly_rate_key") or "",
        "assembly_parts": list(members[0].get("assembly_parts") or []),
        "scope": members[0]["scope"],
        "measured": measured,
        "override": override,
        "effective": effective,
        "sheet_number": ", ".join(sheets),
        "sheet_title": ", ".join(titles),
        "pdf_page": ", ".join(pages),
        "location": "; ".join(row["location"] for row in members if row.get("location")),
        "how_counted": how_counted(members, excluded_reason),
        "confidence": combine_confidence(flags),
        "detector_score": detector_score,
        "sheets": sheets,
        "panel": members[0].get("panel") or "",
        "circuit": members[0].get("circuit") or "",
        "type_mark": _joined(members, "type_mark"),
        "type_description": _joined(members, "type_description"),
        "mounting": _joined(members, "mounting"),
        "mounting_height": _joined(members, "mounting_height"),
        "voltage": _joined(members, "voltage"),
        "circuit_description": _joined(members, "circuit_description"),
        "breaker": _joined(members, "breaker"),
        "poles": _joined(members, "poles"),
        "schedule_load": _joined(members, "schedule_load"),
        "link_flag": "; ".join(link_flags),
        "deduped": any(row.get("deduped") for row in members),
        "material_unit_cost": _same_number([row.get("material_unit_cost") for row in members]),
        "labor_hours": _same_number([row.get("labor_hours") for row in members]),
        "labor_rate_key": rate_keys.pop() if len(rate_keys) == 1 else "",
        "direct_cost": _sum_decimal([row.get("direct_cost") for row in members]),
        "excluded_reason": excluded_reason,
        "zone": members[0].get("zone_label") or "",
        "zone_hold": members[0].get("zone_hold") or "",
        "alternate_side": members[0].get("alternate_side") or "",
        "counts_in_base": not (members[0].get("zone_hold") or ""),
        "zone_conflict_text": members[0].get("zone_conflict_text") or "",
    }


def quantity_lines(rows: list[dict]) -> list[dict]:
    """Group counted rows that share a type, tag, and count method."""

    groups: list[list[dict]] = []
    index: dict[tuple, int] = {}
    for row in rows:
        if row.get("effective") is None:
            continue
        key = (
            row["label"],
            row["category"],
            row.get("panel") or "",
            row.get("circuit") or "",
            row.get("type_mark") or "",
            tuple(row.get("count_methods") or [row.get("count_method") or ""]),
            row.get("zone_hold") or "",
            tuple(row.get("zone_key") or ()),
            tuple(
                f"{item.get('type')}:{','.join(item.get('names') or [])}"
                for item in (row.get("zone_conflicts") or [])
            ),
        )
        if key not in index:
            index[key] = len(groups)
            groups.append([])
        groups[index[key]].append(row)
    return [line_from_members(members) for members in groups]


def excluded_lines(rows: list[dict]) -> list[dict]:
    """One line per item that is not in the counted total."""

    lines = []
    for row in rows:
        if row.get("effective") is not None:
            continue
        if row.get("status") == "pending" or row.get("review_status") == "pending":
            continue
        reason = exclusion_reason(row)
        lines.append(line_from_members([row], excluded_reason=reason))
    return lines
