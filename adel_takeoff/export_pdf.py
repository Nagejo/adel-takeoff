"""Marked-up drawing. Highlights are labels, not a conduit route."""

from __future__ import annotations

import fitz

from adel_takeoff.constants import CONTRACTOR, DISCLAIMER
from adel_takeoff.present import build_review

# Measured, deduped, and corrected marks use different strokes so a reviewer
# can tell them apart on the sheet.
MEASURED_COLOR = (0.75, 0.45, 0.0)
DEDUPED_COLOR = (0.1, 0.25, 0.7)
CORRECTED_COLOR = (0.85, 0.3, 0.05)
UNMATCHED_COLOR = (0.7, 0.1, 0.1)
BY_OTHERS_COLOR = (0.35, 0.35, 0.35)


def build_markup_pdf(pdf_bytes: bytes, job, items, allowances, rfis, claims) -> bytes:
    review = build_review(job, items, allowances, rfis, claims)
    by_id = {row["id"]: row for row in review["rows"]}
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for item in items:
            row = by_id[item["id"]]
            color, label = _mark(row)
            for occurrence in item["occurrences"]:
                page = document[occurrence["page_index"]]
                rect = fitz.Rect(
                    occurrence["x0"] - 3,
                    occurrence["y0"] - 3,
                    occurrence["x1"] + 3,
                    occurrence["y1"] + 3,
                )
                page.draw_rect(rect, color=color, width=1.2)
                page.insert_text(
                    fitz.Point(rect.x1 + 2, rect.y0 + 8),
                    label,
                    fontsize=7,
                    color=color,
                )
        stamp = f"{CONTRACTOR}. {DISCLAIMER}"
        for page in document:
            box = fitz.Rect(36, page.rect.height - 32, page.rect.width - 36, page.rect.height - 12)
            page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=0.4)
            page.insert_textbox(box, stamp, fontsize=8, color=(0, 0, 0), align=fitz.TEXT_ALIGN_LEFT)
        return document.tobytes()
    finally:
        document.close()


def _mark(row: dict) -> tuple[tuple[float, float, float], str]:
    if row["status"] == "unmatched":
        return UNMATCHED_COLOR, "uncounted"
    if row["status"] == "by_others":
        return BY_OTHERS_COLOR, "by others"
    if row["status"] == "not_counted":
        return BY_OTHERS_COLOR, "not counted"
    sheets = ", ".join(row["sheets"])
    if row["override"] is not None:
        measured = row["measured"]
        return (
            CORRECTED_COLOR,
            f"corrected count {row['override']}; measured {measured}",
        )
    if row["deduped"]:
        return DEDUPED_COLOR, f"counted once; sheets {sheets}; measured"
    return MEASURED_COLOR, "measured"
