"""Turn stored rows into the review a person sees."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from adel_takeoff.assemblies import (
    apply_assemblies,
    assembly_warning,
    catalog_for_job,
    labor_rows,
    material_rows,
)
from adel_takeoff.scale import report_for_job
from adel_takeoff.classify import categorize, in_scope, scope_label
from adel_takeoff.confidence import apply_discrepancy_cap, confidence_band
from adel_takeoff.constants import DISCREPANCY_CONFIDENCE_CAP
from adel_takeoff.costing import apply_markup, journeyman_warning, line_direct_cost
from adel_takeoff.extract import read_pdf
from adel_takeoff.lines import excluded_lines, how_counted, quantity_lines
from adel_takeoff.schedule_link import attach_schedules


EXCLUSION_LABELS = {
    "existing_to_remain": "existing to remain",
    "nic": "NIC",
    "by_others": "by others",
}


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _quantity(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _parse_score(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _unique(values) -> list[str]:
    found: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in found:
            found.append(text)
    return found


def location_text(occurrences: list[dict]) -> str:
    parts = []
    for occurrence in occurrences:
        x0 = float(occurrence["x0"])
        y0 = float(occurrence["y0"])
        x1 = float(occurrence["x1"])
        y1 = float(occurrence["y1"])
        piece = f"({(x0 + x1) / 2:.1f}, {(y0 + y1) / 2:.1f}) pt"
        grid = occurrence.get("grid") or ""
        if grid:
            piece = f"grid {grid}; {piece}"
        sheet = occurrence.get("sheet") or ""
        parts.append(f"{sheet} {piece}".strip())
    return "; ".join(parts)


def _decision_label(review_status: str, exclusion: str, status: str) -> str:
    if review_status == "rejected":
        return "rejected"
    if exclusion in EXCLUSION_LABELS:
        return EXCLUSION_LABELS[exclusion]
    if review_status == "corrected":
        return "corrected"
    if status == "by_others":
        return "by others"
    if status == "unmatched":
        return "unmatched"
    if status == "not_counted":
        return "not counted"
    return "accepted"


def present_item(item: dict, job: dict) -> dict:
    label = item["label"]
    detected_label = item["label"]
    category = item["category"]
    panel = item["panel"] or ""
    circuit = item["circuit"] or ""
    link_flag = item["link_flag"] or ""
    review_status = item.get("review_status") or "accepted"
    exclusion = item.get("exclusion") or ""
    measurement = item["measurement"]
    corrected_label = item.get("corrected_label") or ""
    if review_status == "corrected":
        if corrected_label:
            label = corrected_label
            category = categorize(label)
        if item.get("corrected_panel") is not None:
            panel = item["corrected_panel"] or ""
        if item.get("corrected_circuit") is not None:
            circuit = item["corrected_circuit"] or ""
        if panel and circuit:
            link_flag = ""
    promoted = (
        measurement != "matched"
        and review_status == "corrected"
        and bool(corrected_label)
    )
    counted_scope = in_scope(
        category,
        include_landscape=bool(job["include_landscape"]),
        include_lighting_control=bool(job["include_lighting_control"]),
    )
    measured = _quantity(item["measured_quantity"])
    override = _quantity(item["override_quantity"])
    if promoted and measured is None:
        measured = 1
    if review_status == "rejected":
        status = "rejected"
        effective = None
        scope_counted = False
    elif exclusion:
        status = "excluded"
        effective = None
        scope_counted = False
    elif measurement != "matched" and not promoted:
        status = "unmatched"
        effective = None
        scope_counted = False
    elif not counted_scope:
        status = "by_others" if scope_label(category, False) == "by others" else "not_counted"
        effective = None
        scope_counted = False
    else:
        status = "counted"
        effective = override if override is not None else measured
        scope_counted = True
    if status == "rejected":
        scope = "rejected"
    elif status == "excluded":
        scope = EXCLUSION_LABELS.get(exclusion, exclusion or "excluded")
    else:
        scope = scope_label(category, scope_counted and status == "counted")
    rate_key = item.get("labor_rate_key") or "journeyman"
    rate = _decimal(job.get(f"rate_{rate_key}"))
    direct = line_direct_cost(
        effective,
        _decimal(item.get("material_unit_cost")),
        _decimal(item.get("labor_hours")),
        rate,
    )
    occurrences = item.get("occurrences") or []
    method = item.get("method") or ("legend_match" if measurement == "matched" else "unmatched")
    count_methods: list[str] = []
    if review_status == "corrected" and corrected_label:
        count_methods.append("corrected_type")
    if method != "unmatched":
        count_methods.append(method)
    if not count_methods:
        count_methods.append(method)
    count_method = count_methods[0]
    score = _parse_score(item.get("match_score"))
    presented = {
        "id": str(item["id"]),
        "label": label,
        "detected_label": detected_label,
        "assembly_id": "",
        "assembly_name": "",
        "assembly_version": "",
        "assembly_confirmed": False,
        "assembly_example": False,
        "assembly_hours_per_unit": None,
        "assembly_labor_hours": None,
        "assembly_rate_key": "",
        "category": category,
        "measurement": measurement,
        "measured": measured,
        "override": override,
        "effective": effective,
        "sheets": item["sheets"],
        "sheet_numbers": _unique(occurrence.get("sheet") or "" for occurrence in occurrences) or list(item["sheets"]),
        "sheet_titles": _unique(occurrence.get("sheet_title") or "" for occurrence in occurrences),
        "pdf_pages": _unique(str(int(occurrence["page_index"]) + 1) for occurrence in occurrences),
        "location": location_text(occurrences),
        "panel": panel,
        "circuit": circuit,
        "type_mark": "",
        "type_description": "",
        "mounting": "",
        "mounting_height": "",
        "voltage": "",
        "circuit_description": "",
        "breaker": "",
        "poles": "",
        "schedule_load": "",
        "link_flag": link_flag,
        "deduped": bool(item["deduped"]),
        "scope": scope,
        "status": status,
        "material_unit_cost": item.get("material_unit_cost"),
        "labor_hours": item.get("labor_hours"),
        "labor_rate_key": rate_key,
        "direct_cost": direct,
        "show_correction": status == "counted",
        "method": method,
        "count_method": count_method,
        "count_methods": count_methods,
        "detector_score": score,
        "confidence": confidence_band(score),
        "review_status": review_status,
        "exclusion": exclusion,
        "decision_label": _decision_label(review_status, exclusion, status),
        "occurrences": occurrences,
    }
    presented["how_counted"] = how_counted([presented])
    return presented


def present_allowance(row: dict, job: dict) -> dict:
    rate_key = row.get("labor_rate_key") or "journeyman"
    rate = _decimal(job.get(f"rate_{rate_key}"))
    quantity = _decimal(row["quantity"])
    direct = line_direct_cost(
        quantity,
        _decimal(row.get("material_unit_cost")),
        _decimal(row.get("labor_hours")),
        rate,
    )
    return {
        "id": row["id"],
        "description": row["description"],
        "quantity": quantity,
        "reason": row["reason"],
        "category": row["category"],
        "material_unit_cost": row.get("material_unit_cost"),
        "labor_hours": row.get("labor_hours"),
        "labor_rate_key": rate_key,
        "direct_cost": direct or Decimal("0"),
    }


def review_totals(rows: list[dict], allowances: list[dict], markup) -> dict:
    power = 0
    lighting = 0
    controls = 0
    direct = Decimal("0")
    for row in rows:
        effective = row["effective"]
        if effective is None:
            continue
        if row["category"] == "power":
            power += effective
        elif row["category"] in ("lighting", "landscape_lighting"):
            lighting += effective
        elif row["category"] == "lighting_control":
            controls += effective
        if row["direct_cost"] is not None:
            direct += row["direct_cost"]
    for allowance in allowances:
        direct += allowance["direct_cost"]
    markup_value = _decimal(markup)
    return {
        "power_devices": power,
        "lighting_fixtures": lighting,
        "lighting_controls": controls,
        "direct_cost": direct,
        "marked_up_total": apply_markup(direct, markup_value),
        "markup": markup_value,
        "journeyman_warning": "",
    }


def _id_key(row: dict) -> tuple:
    text = str(row["id"])
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _schedule_pages(job: dict):
    raw = job.get("pdf_path") or ""
    if not raw:
        return []
    path = Path(raw)
    if not path.is_file():
        return []
    return read_pdf(path.read_bytes())


def build_review(job: dict, items: list[dict], allowances: list[dict], rfis, claims) -> dict:
    rows = sorted((present_item(item, job) for item in items), key=_id_key)
    linked = attach_schedules(rows, _schedule_pages(job))
    affected = {
        item_id
        for entry in linked["discrepancies"]
        for item_id in entry["item_ids"]
    }
    for row in rows:
        if row["id"] in affected:
            row["confidence"] = apply_discrepancy_cap(row["confidence"])
    catalog = catalog_for_job(job)
    apply_assemblies(rows, job, catalog)
    allowance_rows = [present_allowance(row, job) for row in allowances]
    totals = review_totals(rows, allowance_rows, job.get("markup"))
    totals["journeyman_warning"] = journeyman_warning(_decimal(job["rate_journeyman"]))
    lines = quantity_lines(rows)
    scale_report = report_for_job(job)
    unresolved = [
        row
        for row in rows
        if row["status"] == "unmatched" or row["link_flag"]
    ]
    return {
        "job": job,
        "rows": rows,
        "lines": lines,
        "excluded": excluded_lines(rows),
        "materials": material_rows(lines, catalog),
        "labor": labor_rows(lines, job),
        "assembly_warning": assembly_warning(lines),
        "scales": scale_report["sheets"],
        "measurements": scale_report["measurements"],
        "allowances": allowance_rows,
        "rfis": rfis,
        "claims": claims,
        "unresolved": unresolved,
        "totals": totals,
        "fixture_rows": linked["fixture_rows"],
        "panel_rows": linked["panel_rows"],
        "schedule_regions": linked["regions"],
        "fixture_table_count": linked["fixture_table_count"],
        "panel_table_count": linked["panel_table_count"],
        "discrepancies": linked["discrepancies"],
        "by_type": linked["by_type"],
        "discrepancy_cap": DISCREPANCY_CONFIDENCE_CAP,
    }
