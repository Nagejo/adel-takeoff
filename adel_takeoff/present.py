"""Turn stored rows into the review a person sees."""

from __future__ import annotations

from decimal import Decimal

from adel_takeoff.classify import in_scope, scope_label
from adel_takeoff.costing import apply_markup, journeyman_warning, line_direct_cost


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _quantity(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def present_item(item: dict, job: dict) -> dict:
    category = item["category"]
    counted = in_scope(
        category,
        include_landscape=bool(job["include_landscape"]),
        include_lighting_control=bool(job["include_lighting_control"]),
    )
    measured = _quantity(item["measured_quantity"])
    override = _quantity(item["override_quantity"])
    if item["measurement"] != "matched":
        status = "unmatched"
        effective = None
        counted = False
    elif not counted:
        status = "by_others" if scope_label(category, False) == "by others" else "not_counted"
        effective = None
    else:
        status = "counted"
        effective = override if override is not None else measured
    rate_key = item.get("labor_rate_key") or "journeyman"
    rate = _decimal(job.get(f"rate_{rate_key}"))
    direct = line_direct_cost(
        effective,
        _decimal(item.get("material_unit_cost")),
        _decimal(item.get("labor_hours")),
        rate,
    )
    return {
        "id": item["id"],
        "label": item["label"],
        "category": category,
        "measurement": item["measurement"],
        "measured": measured,
        "override": override,
        "effective": effective,
        "sheets": item["sheets"],
        "panel": item["panel"] or "",
        "circuit": item["circuit"] or "",
        "link_flag": item["link_flag"] or "",
        "deduped": bool(item["deduped"]),
        "scope": scope_label(category, counted and status == "counted"),
        "status": status,
        "material_unit_cost": item.get("material_unit_cost"),
        "labor_hours": item.get("labor_hours"),
        "labor_rate_key": rate_key,
        "direct_cost": direct,
        "show_correction": status == "counted",
    }


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


def build_review(job: dict, items: list[dict], allowances: list[dict], rfis, claims) -> dict:
    rows = [present_item(item, job) for item in items]
    allowance_rows = [present_allowance(row, job) for row in allowances]
    totals = review_totals(rows, allowance_rows, job.get("markup"))
    totals["journeyman_warning"] = journeyman_warning(_decimal(job["rate_journeyman"]))
    unresolved = [
        row
        for row in rows
        if row["status"] == "unmatched" or row["link_flag"]
    ]
    return {
        "job": job,
        "rows": rows,
        "allowances": allowance_rows,
        "rfis": rfis,
        "claims": claims,
        "unresolved": unresolved,
        "totals": totals,
    }
