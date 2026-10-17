"""Marked-up drawing. Highlights are labels, not a conduit route."""

from __future__ import annotations

import fitz

from adel_takeoff.constants import CONTRACTOR, DISCLAIMER
from adel_takeoff.present import build_review

# Measured, deduped, and corrected marks use different strokes so a reviewer
# can tell them apart on the sheet. Rejected and excluded marks are dashed.
MEASURED_COLOR = (0.75, 0.45, 0.0)
DEDUPED_COLOR = (0.1, 0.25, 0.7)
CORRECTED_COLOR = (0.85, 0.3, 0.05)
UNMATCHED_COLOR = (0.7, 0.1, 0.1)
BY_OTHERS_COLOR = (0.35, 0.35, 0.35)
REJECTED_COLOR = (0.55, 0.0, 0.0)
EXCLUDED_COLOR = (0.25, 0.25, 0.25)
SCHEDULE_COLOR = (0, 0.45, 0.45)
ZONE_COLORS = {
    "alternate": (0.85, 0.45, 0.05),
    "phase": (0.12, 0.28, 0.86),
    "floor": (0.10, 0.55, 0.25),
    "area": (0.45, 0.20, 0.65),
    "by_others": (0.35, 0.35, 0.35),
}


def build_markup_pdf(pdf_bytes: bytes, job, items, allowances, rfis, claims) -> bytes:
    review = build_review(job, items, allowances, rfis, claims)
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for row in review["rows"]:
            color, phrase, dashed = _mark(row)
            label = f"{row['id']} {phrase}"
            for occurrence in row["occurrences"]:
                page = document[occurrence["page_index"]]
                rect = fitz.Rect(
                    occurrence["x0"] - 3,
                    occurrence["y0"] - 3,
                    occurrence["x1"] + 3,
                    occurrence["y1"] + 3,
                )
                page.draw_rect(
                    rect,
                    color=color,
                    width=1.2,
                    dashes="[3 2] 0" if dashed else None,
                )
                _draw_number(page, rect, str(row["id"]), color)
                page.insert_text(
                    fitz.Point(rect.x1 + 4, rect.y0 + 8),
                    label,
                    fontsize=7,
                    fontname="helv",
                    color=color,
                )
        _draw_zones(document, review.get("zones") or [])
        for region in review.get("schedule_regions") or []:
            page = document[region["page_index"]]
            x0, y0, x1, y1 = region["bbox"]
            rect = fitz.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 4)
            page.draw_rect(rect, color=SCHEDULE_COLOR, width=1.0)
            page.insert_text(
                fitz.Point(rect.x0, max(12, rect.y0 - 2)),
                region["label"],
                fontsize=8,
                fontname="helv",
                color=SCHEDULE_COLOR,
            )
        stamp = f"{CONTRACTOR}. {DISCLAIMER}"
        for page in document:
            box = fitz.Rect(36, page.rect.height - 32, page.rect.width - 36, page.rect.height - 12)
            page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=0.4)
            page.insert_textbox(box, stamp, fontsize=8, color=(0, 0, 0), align=fitz.TEXT_ALIGN_LEFT)
        return document.tobytes()
    finally:
        document.close()


def _draw_zones(document, zones: list[dict]) -> None:
    from adel_takeoff.zones import type_label

    for zone in zones:
        page_index = int(zone["page_index"])
        if page_index < 0 or page_index >= document.page_count:
            continue
        page = document[page_index]
        points = [fitz.Point(point[0], point[1]) for point in zone["points"]]
        color = ZONE_COLORS.get(zone["type"], (0.2, 0.2, 0.2))
        page.draw_polyline(points, color=color, width=1.4, closePath=True)
        left = min(point.x for point in points)
        top = min(point.y for point in points)
        label = f"{zone['name']} · {type_label(zone['type'])}"
        if zone.get("alternate"):
            label += f" ({zone['alternate']})"
        page.insert_text(
            fitz.Point(left, max(12, top - 4)),
            label,
            fontsize=8,
            fontname="helv",
            color=color,
        )


def _draw_number(page, rect: fitz.Rect, item_id: str, color) -> None:
    center_x = min(max(rect.x0, 12), page.rect.width - 12)
    center_y = min(max(rect.y0 - 8, 12), page.rect.height - 12)
    page.draw_circle(
        fitz.Point(center_x, center_y),
        7,
        color=color,
        fill=(1, 1, 1),
        width=0.9,
    )
    offset = 2.4 * len(item_id)
    page.insert_text(
        fitz.Point(center_x - offset, center_y + 2.6),
        item_id,
        fontsize=8,
        fontname="helv",
        color=color,
    )


def _mark(row: dict) -> tuple[tuple[float, float, float], str, bool]:
    if row["status"] == "rejected" or row["review_status"] == "rejected":
        return REJECTED_COLOR, "rejected", True
    if row["exclusion"] == "existing_to_remain":
        return EXCLUDED_COLOR, "existing to remain", True
    if row["exclusion"] == "nic":
        return EXCLUDED_COLOR, "NIC", True
    if row["exclusion"] == "by_others":
        return BY_OTHERS_COLOR, "by others", True
    if row["status"] == "unmatched":
        return UNMATCHED_COLOR, "uncounted", False
    if row["status"] == "by_others":
        return BY_OTHERS_COLOR, "by others", False
    if row["status"] == "not_counted":
        return BY_OTHERS_COLOR, "not counted", False
    if row["status"] == "excluded":
        return EXCLUDED_COLOR, row["scope"], True
    sheets = ", ".join(row["sheets"])
    if row["override"] is not None:
        measured = row["measured"]
        return (
            CORRECTED_COLOR,
            f"corrected count {row['override']}; measured {measured}",
            False,
        )
    if row["deduped"]:
        return DEDUPED_COLOR, f"counted once; sheets {sheets}; measured", False
    if row["review_status"] == "corrected":
        return CORRECTED_COLOR, "type corrected", False
    if row.get("scope_mode") == "rough_in":
        return MEASURED_COLOR, "rough-in only", False
    return MEASURED_COLOR, "measured", False
