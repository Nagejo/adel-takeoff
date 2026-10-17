"""Excel quantity report. Direct cost only. No price book and no routing."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font

from adel_takeoff.confidence import ConfidenceThresholds
from adel_takeoff.constants import CONTRACTOR, DISCLAIMER, LABOR_RATES
from adel_takeoff.costing import journeyman_warning
from adel_takeoff.present import build_review


def _number(value: Decimal | None):
    if value is None:
        return None
    return float(value)


def _score(value):
    if value is None or value == "":
        return None
    return float(value)


QUANTITY_HEADERS = [
    "Line",
    "Item IDs",
    "Item",
    "Category",
    "Scope",
    "Measured quantity",
    "Count correction",
    "Effective quantity",
    "Sheet number",
    "Sheet title",
    "PDF page",
    "Location",
    "How counted",
    "Confidence",
    "Detector score",
    "Sheets",
    "Panel",
    "Circuit",
    "Review flag",
    "Deduped",
    "Material unit cost",
    "Labor hours",
    "Labor rate",
    "Direct cost",
]


def build_workbook(job: dict, items, allowances, rfis, claims) -> bytes:
    review = build_review(job, items, allowances, rfis, claims)
    workbook = Workbook()
    workbook.properties.creator = CONTRACTOR
    workbook.properties.title = "Quantity takeoff"
    sheet = workbook.active
    sheet.title = "Quantities"

    sheet["A1"] = CONTRACTOR
    sheet["A1"].font = Font(bold=True, size=14)
    sheet["A2"] = "Quantity takeoff"
    sheet["A3"] = DISCLAIMER
    sheet["A4"] = f"Job: {job['name']}"
    permit = job.get("permit_issue_date") or ""
    sheet["A5"] = (
        f"Permit-issue date: {permit or 'not set'}. "
        f"Code edition basis: {job['code_basis']}. "
        f"{job['code_fbc']}. {job['code_nec']}."
    )
    if review["totals"]["markup"] is None:
        sheet["A6"] = "Markup: not applied"
    else:
        sheet["A6"] = f"Markup: {format(review['totals']['markup'], 'f')}"
    warning = journeyman_warning(Decimal(job["rate_journeyman"]))
    sheet["A7"] = warning
    sheet["A8"] = (
        f"Power devices: {review['totals']['power_devices']}. "
        f"Lighting fixtures: {review['totals']['lighting_fixtures']}."
    )
    limits = ConfidenceThresholds.from_config()
    sheet["A9"] = (
        f"Confidence: high at or above {limits.high_min}, "
        f"check at or above {limits.check_min}, otherwise low. "
        "Blank means the detector recorded no score."
    )
    sheet["A10"] = (
        "Numbered marks on the PDF use the item id. "
        "A quantity line with one item uses that same id."
    )

    sheet["A12"] = "Labor rates"
    rate_row = 13
    stored = {
        "journeyman": job["rate_journeyman"],
        "foreman": job["rate_foreman"],
        "pm": job["rate_pm"],
        "apprentice": job["rate_apprentice"],
    }
    for key, label, _default in LABOR_RATES:
        sheet.cell(rate_row, 1, label)
        sheet.cell(rate_row, 2, float(Decimal(stored[key])))
        sheet.cell(rate_row, 3, "per hour")
        rate_row += 1

    header_row = rate_row + 1
    for column, header in enumerate(QUANTITY_HEADERS, start=1):
        cell = sheet.cell(header_row, column, header)
        cell.font = Font(bold=True)

    row_index = header_row + 1
    rate_labels = {key: label for key, label, _default in LABOR_RATES}
    for line in review["lines"]:
        _write_quantity_row(sheet, row_index, line, rate_labels)
        row_index += 1

    row_index += 1
    sheet.cell(row_index, 1, "Allowances").font = Font(bold=True)
    row_index += 1
    for column, header in enumerate(
        ["Description", "Quantity", "Reason", "Category", "Material unit cost", "Labor hours", "Labor rate", "Direct cost"],
        start=1,
    ):
        sheet.cell(row_index, column, header).font = Font(bold=True)
    row_index += 1
    for allowance in review["allowances"]:
        values = [
            allowance["description"],
            _number(allowance["quantity"]),
            allowance["reason"],
            allowance["category"],
            _number(_decimal_or_none(allowance["material_unit_cost"])),
            _number(_decimal_or_none(allowance["labor_hours"])),
            rate_labels.get(allowance["labor_rate_key"], allowance["labor_rate_key"]),
            _number(allowance["direct_cost"]),
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row_index, column, value)
        row_index += 1

    row_index += 1
    sheet.cell(row_index, 1, "Job direct cost")
    sheet.cell(row_index, 2, _number(review["totals"]["direct_cost"]))
    row_index += 1
    sheet.cell(row_index, 1, "Marked-up total")
    if review["totals"]["marked_up_total"] is None:
        sheet.cell(row_index, 2, "not applied")
    else:
        sheet.cell(row_index, 2, _number(review["totals"]["marked_up_total"]))

    excluded = workbook.create_sheet("Excluded")
    excluded_headers = [
        "Item ID",
        "Item",
        "Category",
        "Sheet number",
        "Sheet title",
        "PDF page",
        "Location",
        "Reason",
        "How counted",
        "Confidence",
        "Detector score",
        "Panel",
        "Circuit",
    ]
    for column, header in enumerate(excluded_headers, start=1):
        cell = excluded.cell(1, column, header)
        cell.font = Font(bold=True)
    for index, line in enumerate(review["excluded"], start=2):
        values = [
            ", ".join(line["item_ids"]),
            line["label"],
            line["category"],
            line["sheet_number"],
            line["sheet_title"],
            line["pdf_page"],
            line["location"],
            line["excluded_reason"],
            line["how_counted"],
            line["confidence"] or None,
            _score(line["detector_score"]),
            line["panel"],
            line["circuit"],
        ]
        for column, value in enumerate(values, start=1):
            excluded.cell(index, column, value if value != "" else None)

    rfi_sheet = workbook.create_sheet("RFIs")
    rfi_sheet.append(["Topic", "Disposition", "Sections", "Detail"])
    for rfi in rfis:
        rfi_sheet.append([rfi["topic"], rfi["disposition"], rfi["sections"], rfi["detail"]])

    claim_sheet = workbook.create_sheet("Code claims")
    claim_sheet.append(["Statement", "Section", "Rendered"])
    for claim in claims:
        claim_sheet.append([claim["statement"], claim["section"], claim["rendered"]])

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_quantity_row(sheet, row_index: int, line: dict, rate_labels: dict) -> None:
    rate_key = line["labor_rate_key"]
    values = [
        line["line_id"],
        ", ".join(line["item_ids"]),
        line["label"],
        line["category"],
        line["scope"],
        line["measured"],
        line["override"],
        line["effective"],
        line["sheet_number"],
        line["sheet_title"] or None,
        line["pdf_page"],
        line["location"],
        line["how_counted"],
        line["confidence"] or None,
        _score(line["detector_score"]),
        ", ".join(line["sheets"]),
        line["panel"],
        line["circuit"],
        line["link_flag"],
        "counted once" if line["deduped"] else "",
        _number(_decimal_or_none(line["material_unit_cost"])),
        _number(_decimal_or_none(line["labor_hours"])),
        rate_labels.get(rate_key, rate_key) if rate_key else None,
        _number(line["direct_cost"]),
    ]
    for column, value in enumerate(values, start=1):
        sheet.cell(row_index, column, value if value is not None else None)


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))
