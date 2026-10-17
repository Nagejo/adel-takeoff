"""Read a sheet scale and measure a polyline in feet.

A scale comes from printed text or a graphic bar, or from a manual entry
the estimator stored on the job. A missing scale, NTS, an ambiguous set of
scales, or a text/bar disagreement refuses the length. Nothing here guesses
a scale, routes conduit, or draws a home run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz

from adel_takeoff.assemblies import data_dir_for_job
from adel_takeoff.constants import POINTS_PER_INCH, SCALE_AGREEMENT_TOLERANCE
from adel_takeoff.extract import (
    OCR_VALUE_NOTE,
    Word,
    group_lines,
    is_low_ocr,
    min_ocr_confidence,
    page_words,
    read_sheet_title,
    sheet_name,
)
from adel_takeoff.scale_store import load_scales, scale_path


class ScaleInputError(ValueError):
    """The estimator sent a scale or a length this job will not store."""


PICKED_SCALES = (
    '1/8" = 1\'-0"',
    '3/16" = 1\'-0"',
    '1/4" = 1\'-0"',
    '1/2" = 1\'-0"',
    '1" = 10\'',
    '1" = 20\'',
    '1" = 30\'',
    "1:50",
    "1:100",
    "1:200",
)

_ARCHITECTURAL = re.compile(
    r"""
    (?P<paper>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)
    \s*(?:\"|”|in(?:ches)?\.?)
    \s*=\s*
    (?P<feet>\d+)
    \s*(?:'|’|ft\.?|feet)
    \s*(?:-\s*(?P<inch>\d+(?:\.\d+)?)\s*(?:\"|”)?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_METRIC = re.compile(r"\b1\s*:\s*(?P<denom>[1-9]\d{0,4})\b", re.IGNORECASE)
_NTS = re.compile(
    r"\b(?:N\.?\s*T\.?\s*S\.?|NOT\s+TO\s+SCALE|NO\s+SCALE)\b",
    re.IGNORECASE,
)
_BAR_LABEL = re.compile(
    r"""
    ^(?P<feet>\d+(?:\.\d+)?)
    \s*(?:'|’|ft\.?|feet)?
    \s*(?:-\s*(?P<inch>\d+(?:\.\d+)?)\s*(?:\"|”)?)?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedScale:
    label: str
    feet_per_point: Decimal | None
    nts: bool
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    conflict: bool = False


def _quantity(text: str) -> Decimal:
    cleaned = " ".join(text.strip().split())
    if " " in cleaned and "/" in cleaned:
        whole, fraction = cleaned.split()
        numerator, denominator = fraction.split("/")
        return Decimal(whole) + (Decimal(numerator) / Decimal(denominator))
    if "/" in cleaned:
        numerator, denominator = cleaned.split("/")
        return Decimal(numerator) / Decimal(denominator)
    return Decimal(cleaned)


def _ratio(real_feet: Decimal, paper_inches: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    if paper_inches <= 0 or real_feet <= 0:
        raise ScaleInputError("A scale needs a positive paper length and a positive real length.")
    denominator = paper_inches * POINTS_PER_INCH
    return real_feet, denominator, real_feet / denominator


def parse_scale_text(text: str) -> ParsedScale | None:
    """Return a scale the text actually states.

    Text that does not state a scale returns None. It is not given a scale.
    """

    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    nts = _NTS.search(raw)
    architectural = _ARCHITECTURAL.search(raw)
    metric = _METRIC.search(raw)
    stated = architectural or metric
    if nts and stated:
        return ParsedScale(label=raw, feet_per_point=None, nts=True, conflict=True)
    if nts:
        return ParsedScale(label="not to scale", feet_per_point=None, nts=True)
    if architectural is None and metric is None:
        return None
    if architectural is not None and metric is not None:
        return ParsedScale(label=raw, feet_per_point=None, nts=False, conflict=True)
    if architectural is not None:
        paper = _quantity(architectural.group("paper"))
        real = Decimal(architectural.group("feet"))
        inches = architectural.group("inch")
        if inches:
            real += Decimal(inches) / Decimal(12)
        if paper <= 0 or real <= 0:
            return None
        label = _architectural_label(architectural.group("paper"), real)
        numerator, denominator, feet_per_point = _ratio(real, paper)
        return ParsedScale(
            label=label,
            feet_per_point=feet_per_point,
            numerator=numerator,
            denominator=denominator,
            nts=False,
        )
    denom = Decimal(metric.group("denom"))
    numerator = denom / Decimal(12)
    return ParsedScale(
        label=f"1:{denom}",
        feet_per_point=numerator / POINTS_PER_INCH,
        numerator=numerator,
        denominator=POINTS_PER_INCH,
        nts=False,
    )


def _architectural_label(paper: str, real_feet: Decimal) -> str:
    paper_text = " ".join(paper.split())
    whole = int(real_feet)
    inches = (real_feet - Decimal(whole)) * Decimal(12)
    if inches == 0:
        return f'{paper_text}" = {whole}\'-0"'
    inch_text = format(inches.normalize(), "f")
    return f'{paper_text}" = {whole}\'-{inch_text}"'


def scales_agree(
    left: Decimal,
    right: Decimal,
    tolerance: Decimal = SCALE_AGREEMENT_TOLERANCE,
) -> bool:
    if left == 0:
        return right == 0
    return abs(left - right) <= tolerance * abs(left)


def polyline_length(
    points: list[tuple[Decimal, Decimal]],
    feet_per_point: Decimal | None = None,
    *,
    numerator: Decimal | None = None,
    denominator: Decimal | None = None,
) -> Decimal:
    """Sum segment lengths. The scale is a fraction so paper points convert exactly."""

    if numerator is None:
        if feet_per_point is None or feet_per_point <= 0:
            raise ScaleInputError("A measured length needs a positive scale.")
        numerator = feet_per_point
        denominator = Decimal(1)
    if denominator is None or denominator <= 0 or numerator <= 0:
        raise ScaleInputError("A measured length needs a positive scale.")
    if len(points) < 2:
        raise ScaleInputError("A measured length needs at least two points.")
    span = Decimal("0")
    for start, end in zip(points, points[1:]):
        dx = abs(end[0] - start[0])
        dy = abs(end[1] - start[1])
        if dx == 0:
            span += dy
        elif dy == 0:
            span += dx
        else:
            span += (dx * dx + dy * dy).sqrt()
    return span * numerator / denominator


def parse_points(text: str) -> list[tuple[Decimal, Decimal]]:
    points = []
    for chunk in re.split(r"[;\n]+", str(text or "")):
        piece = chunk.strip()
        if not piece:
            continue
        parts = [item.strip() for item in piece.split(",")]
        if len(parts) != 2:
            raise ScaleInputError("Each point needs an x and a y, such as 100,400.")
        points.append((_decimal_value(parts[0], "Point"), _decimal_value(parts[1], "Point")))
    if len(points) < 2:
        raise ScaleInputError("A measured length needs at least two points.")
    return points


def _decimal_value(raw: str, label: str) -> Decimal:
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ScaleInputError(f"{label} must be a number.") from exc
    if not value.is_finite():
        raise ScaleInputError(f"{label} must be a number.")
    return value


def _line_text(words: list[Word]) -> tuple[str, list[tuple[int, int, Word]]]:
    parts: list[str] = []
    spans = []
    offset = 0
    for index, word in enumerate(words):
        if index:
            parts.append(" ")
            offset += 1
        start = offset
        parts.append(word.text)
        offset += len(word.text)
        spans.append((start, offset, word))
    return "".join(parts), spans


def _span_words(spans, start: int, end: int) -> list[Word]:
    return [word for word_start, word_end, word in spans if word_end > start and word_start < end]


def _bbox_for_span(spans, start: int, end: int) -> tuple[float, float, float, float] | None:
    chosen = _span_words(spans, start, end)
    if not chosen:
        return None
    return (
        min(word.x0 for word in chosen),
        min(word.y0 for word in chosen),
        max(word.x1 for word in chosen),
        max(word.y1 for word in chosen),
    )


def _ocr_fields(words: list[Word]) -> dict:
    if not any(word.source == "ocr" for word in words):
        return {"text_source": "pdf", "ocr_check": False, "confidence": None}
    low = any(is_low_ocr(word) for word in words)
    return {
        "text_source": "ocr",
        "ocr_check": low,
        "confidence": min_ocr_confidence(words),
    }


def _text_readings(words: list[Word], page_index: int) -> list[dict]:
    readings = []
    for line in group_lines(words):
        text, spans = _line_text(line)
        nts_spans = list(_NTS.finditer(text))
        scale_spans = list(_ARCHITECTURAL.finditer(text)) + list(_METRIC.finditer(text))
        if nts_spans and scale_spans:
            boxes = [_bbox_for_span(spans, match.start(), match.end()) for match in nts_spans + scale_spans]
            boxes = [box for box in boxes if box is not None]
            involved = []
            for match in nts_spans + scale_spans:
                involved.extend(_span_words(spans, match.start(), match.end()))
            ocr = _ocr_fields(involved)
            if ocr["ocr_check"]:
                readings.append(
                    _reading(
                        kind="text",
                        label=text,
                        feet_per_point=None,
                        nts=False,
                        conflict=False,
                        reason=OCR_VALUE_NOTE,
                        bbox=_union(boxes) if boxes else (0, 0, 0, 0),
                        page_index=page_index,
                        **ocr,
                    )
                )
            else:
                readings.append(
                    _reading(
                        kind="text",
                        label=text,
                        feet_per_point=None,
                        nts=True,
                        conflict=True,
                        reason="The text states a scale and not to scale. Lengths are not measured.",
                        bbox=_union(boxes) if boxes else (0, 0, 0, 0),
                        page_index=page_index,
                        **ocr,
                    )
                )
            continue
        if nts_spans and not scale_spans:
            for match in nts_spans:
                box = _bbox_for_span(spans, match.start(), match.end())
                if box is None:
                    continue
                parsed = parse_scale_text(match.group(0))
                ocr = _ocr_fields(_span_words(spans, match.start(), match.end()))
                if ocr["ocr_check"]:
                    readings.append(
                        _reading(
                            kind="text",
                            label=parsed.label if parsed else "not to scale",
                            feet_per_point=None,
                            nts=False,
                            conflict=False,
                            reason=OCR_VALUE_NOTE,
                            bbox=box,
                            page_index=page_index,
                            **ocr,
                        )
                    )
                    continue
                readings.append(
                    _reading(
                        kind="text",
                        label=parsed.label if parsed else "not to scale",
                        feet_per_point=None,
                        nts=True,
                        conflict=False,
                        reason="",
                        bbox=box,
                        page_index=page_index,
                        **ocr,
                    )
                )
            continue
        for match in scale_spans:
            parsed = parse_scale_text(match.group(0))
            box = _bbox_for_span(spans, match.start(), match.end())
            if parsed is None or box is None or parsed.feet_per_point is None:
                continue
            ocr = _ocr_fields(_span_words(spans, match.start(), match.end()))
            if ocr["ocr_check"]:
                readings.append(
                    _reading(
                        kind="text",
                        label=parsed.label,
                        feet_per_point=None,
                        nts=False,
                        conflict=False,
                        reason=OCR_VALUE_NOTE,
                        bbox=box,
                        page_index=page_index,
                        **ocr,
                    )
                )
                continue
            readings.append(
                _reading(
                    kind="text",
                    label=parsed.label,
                    feet_per_point=parsed.feet_per_point,
                    numerator=parsed.numerator,
                    denominator=parsed.denominator,
                    nts=False,
                    conflict=parsed.conflict,
                    reason="The text states more than one scale. Lengths are not measured." if parsed.conflict else "",
                    bbox=box,
                    page_index=page_index,
                    **ocr,
                )
            )
    return readings


def _bar_label(text: str) -> Decimal | None:
    match = _BAR_LABEL.fullmatch(text.strip())
    if match is None:
        return None
    feet = Decimal(match.group("feet"))
    inches = match.group("inch")
    if inches:
        feet += Decimal(inches) / Decimal(12)
    return feet


def _graphic_bars(page, page_index: int) -> list[dict]:
    horizontals = []
    ticks = []
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if item[0] != "l":
                continue
            start, end = item[1], item[2]
            x0, x1 = float(start.x), float(end.x)
            y0, y1 = float(start.y), float(end.y)
            if abs(y0 - y1) <= 1.5 and abs(x1 - x0) >= 36:
                horizontals.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            elif abs(x0 - x1) <= 1.5 and 6 <= abs(y1 - y0) <= 30:
                ticks.append(( (x0 + x1) / 2, min(y0, y1), max(y0, y1) ))
    words = page_words(page)
    readings = []
    for x0, x1, bar_y in horizontals:
        used_ticks = [
            tick
            for tick in ticks
            if x0 - 2 <= tick[0] <= x1 + 2 and tick[1] - 1 <= bar_y <= tick[2] + 1
        ]
        labeled = []
        for word in words:
            if is_low_ocr(word):
                continue
            feet = _bar_label(word.text)
            if feet is None:
                continue
            if abs(((word.y0 + word.y1) / 2) - bar_y) > 28:
                continue
            center = (word.x0 + word.x1) / 2
            nearest = min(used_ticks, key=lambda tick: abs(tick[0] - center), default=None)
            if nearest is None or abs(nearest[0] - center) > 14:
                continue
            labeled.append((nearest[0], feet, word))
        if len({round(item[0], 2) for item in labeled}) < 2:
            continue
        by_tick: dict[float, tuple] = {}
        for tick_x, feet, word in labeled:
            current = by_tick.get(round(tick_x, 2))
            if current is None or abs(tick_x - (word.x0 + word.x1) / 2) < current[0]:
                by_tick[round(tick_x, 2)] = (abs(tick_x - (word.x0 + word.x1) / 2), tick_x, feet, word)
        points = sorted(by_tick.values(), key=lambda item: item[1])
        left, right = points[0], points[-1]
        span_pt = Decimal(str(right[1])) - Decimal(str(left[1]))
        span_ft = abs(right[2] - left[2])
        if span_pt <= 0 or span_ft == 0:
            continue
        feet_per_point = span_ft / span_pt
        numerator = span_ft
        denominator = span_pt
        consistent = True
        for _gap, tick_x, feet, _word in points[1:]:
            expected = left[2] + (Decimal(str(tick_x)) - Decimal(str(left[1]))) * feet_per_point
            if not scales_agree(expected, feet):
                consistent = False
        boxes = [(x0, bar_y, x1, bar_y)] + [
            (item[3].x0, item[3].y0, item[3].x1, item[3].y1) for item in points
        ]
        label = f"graphic bar {format(left[2], 'f')} to {format(right[2], 'f')} ft"
        if not consistent:
            readings.append(
                _reading(
                    kind="graphic bar",
                    label=label,
                    feet_per_point=None,
                    nts=False,
                    conflict=True,
                    reason="The graphic bar labels do not make one scale. Lengths are not measured.",
                    bbox=_union(boxes),
                    page_index=page_index,
                )
            )
            continue
        readings.append(
            _reading(
                    kind="graphic bar",
                    label=label,
                    feet_per_point=feet_per_point,
                    numerator=numerator,
                    denominator=denominator,
                    nts=False,
                    conflict=False,
                reason="",
                bbox=_union(boxes),
                page_index=page_index,
            )
        )
    return readings


def _viewport_rects(page) -> list[tuple[float, float, float, float]]:
    limit = float(page.rect.width) * float(page.rect.height) * 0.85
    rects = []
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if item[0] != "re":
                continue
            rect = item[1]
            width = float(rect.width)
            height = float(rect.height)
            if width < 80 or height < 60:
                continue
            if width * height > limit:
                continue
            rects.append((float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)))
    return rects


def _reading(**fields) -> dict:
    fields.setdefault("numerator", None)
    fields.setdefault("denominator", None)
    fields.setdefault("text_source", "pdf")
    fields.setdefault("ocr_check", False)
    fields.setdefault("confidence", None)
    return fields


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _uniform(readings: list[dict]) -> bool:
    if any(reading["conflict"] for reading in readings):
        return False
    nts = [reading for reading in readings if reading["nts"]]
    scaled = [reading for reading in readings if reading["feet_per_point"] is not None]
    if nts and scaled:
        return False
    if nts:
        return True
    values = [reading["feet_per_point"] for reading in scaled]
    if not values:
        return False
    return all(scales_agree(values[0], value) for value in values[1:])


def _assign_viewport(rects, reading) -> tuple:
    box = reading["bbox"]
    center_x = (box[0] + box[2]) / 2
    center_y = (box[1] + box[3]) / 2
    hits = [
        rect
        for rect in rects
        if rect[0] <= center_x <= rect[2] and rect[1] <= center_y <= rect[3]
    ]
    if not hits:
        return ("sheet", None)
    return ("viewport", min(hits, key=lambda rect: (rect[2] - rect[0]) * (rect[3] - rect[1])))


def _format_bbox(bbox) -> str:
    if not bbox:
        return ""
    return f"{bbox[0]:.2f},{bbox[1]:.2f} to {bbox[2]:.2f},{bbox[3]:.2f}"


def _public_reading(reading: dict) -> dict:
    return {
        "kind": reading["kind"],
        "label": reading["label"],
        "bbox": [round(float(value), 2) for value in reading["bbox"]],
        "nts": reading["nts"],
        "conflict": reading["conflict"],
        "text_source": reading.get("text_source") or "pdf",
    }


def _text_origin(group: list[dict]) -> str:
    if any(reading.get("text_source") == "ocr" for reading in group):
        return "ocr"
    return "pdf"


def _region(group: list[dict], applies_to: str, viewport_bbox, page_index: int) -> dict:
    texts = [
        reading
        for reading in group
        if reading["kind"] == "text" and reading["feet_per_point"] is not None and not reading["conflict"]
    ]
    bars = [
        reading
        for reading in group
        if reading["kind"] == "graphic bar" and reading["feet_per_point"] is not None and not reading["conflict"]
    ]
    nts = [reading for reading in group if reading["nts"] and not reading["conflict"]]
    conflicts = [reading for reading in group if reading["conflict"]]
    read_from = [_public_reading(reading) for reading in group]
    base = {
        "page_index": page_index,
        "applies_to": applies_to,
        "viewport": [round(float(value), 2) for value in viewport_bbox] if viewport_bbox else None,
        "read_from": read_from,
        "set_by": "",
        "set_at": "",
    }
    if conflicts:
        return {
            **base,
            "status": "conflict",
            "source": "",
            "label": "",
            "feet_per_point": "",
            "numerator": None,
            "denominator": None,
            "reason": conflicts[0]["reason"],
        }
    if nts and (texts or bars):
        return {
            **base,
            "status": "ambiguous",
            "source": "",
            "label": "",
            "feet_per_point": "",
            "numerator": None,
            "denominator": None,
            "reason": (
                "This region is marked not to scale and also states a scale. "
                "Lengths are not measured."
            ),
        }
    checks = [reading for reading in group if reading.get("ocr_check")]
    if nts and not texts and not bars:
        return {
            **base,
            "status": "nts",
            "source": "ocr" if _text_origin(group) == "ocr" else "text",
            "label": "not to scale",
            "feet_per_point": "",
            "numerator": None,
            "denominator": None,
            "reason": "This region is marked not to scale. Lengths are not measured.",
            "confidence": "",
            "ocr_confidence": None,
        }
    text_value = _one_value(texts)
    bar_value = _one_value(bars)
    if text_value == "split" or bar_value == "split" or (texts and bars and text_value != "split" and bar_value != "split" and text_value is not None and bar_value is not None and not scales_agree(text_value, bar_value)):
        if texts and bars and text_value not in (None, "split") and bar_value not in (None, "split") and not scales_agree(text_value, bar_value):
            return {
                **base,
                "status": "conflict",
                "source": "",
                "label": "",
                "feet_per_point": "",
                "reason": (
                    f"The printed scale {texts[0]['label']} and the graphic bar "
                    f"{bars[0]['label']} disagree. Lengths are not measured."
                ),
            }
        return {
            **base,
            "status": "ambiguous",
            "source": "",
            "label": "",
            "feet_per_point": "",
            "numerator": None,
            "denominator": None,
            "reason": (
                "More than one scale is printed and they are not tied to separate "
                "viewports. Lengths are not measured."
            ),
        }
    if checks and not texts and not bars:
        return {
            **base,
            "status": "check",
            "source": "ocr",
            "label": checks[0]["label"],
            "feet_per_point": "",
            "numerator": None,
            "denominator": None,
            "reason": checks[0]["reason"] or OCR_VALUE_NOTE,
            "confidence": "check",
            "ocr_confidence": checks[0].get("confidence"),
        }
    if not texts and not bars:
        return _missing_region(page_index, applies_to, viewport_bbox)
    chosen = texts[0] if texts else bars[0]
    if texts and texts[0].get("text_source") == "ocr":
        source = "ocr"
    elif texts and bars:
        source = "text and graphic bar"
    elif texts:
        source = "text"
    else:
        source = "graphic bar"
    return {
        **base,
        "status": "valid",
        "source": source,
        "label": chosen["label"] if texts else bars[0]["label"],
        "feet_per_point": chosen["feet_per_point"],
        "numerator": chosen["numerator"],
        "denominator": chosen["denominator"],
        "reason": "",
    }


def _one_value(readings: list[dict]):
    if not readings:
        return None
    values = [reading["feet_per_point"] for reading in readings]
    if any(not scales_agree(values[0], value) for value in values[1:]):
        return "split"
    return values[0]


def _missing_region(page_index: int, applies_to: str = "sheet", viewport_bbox=None) -> dict:
    if applies_to == "sheet" and viewport_bbox is None:
        reason = "No scale is printed on this sheet. Lengths are not measured."
    else:
        reason = "No scale is printed for this part of the sheet. Lengths are not measured."
    return {
        "page_index": page_index,
        "status": "missing",
        "source": "",
        "label": "",
        "feet_per_point": "",
        "numerator": None,
        "denominator": None,
        "applies_to": applies_to,
        "viewport": [round(float(value), 2) for value in viewport_bbox] if viewport_bbox else None,
        "read_from": [],
        "reason": reason,
        "set_by": "",
        "set_at": "",
    }


def _build_regions(readings: list[dict], rects, page_index: int) -> list[dict]:
    if not readings:
        return [_missing_region(page_index)]
    if _uniform(readings):
        return [_region(readings, "sheet", None, page_index)]
    buckets: dict[tuple, list] = {}
    order: list[tuple] = []
    for reading in readings:
        key = _assign_viewport(rects, reading)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(reading)
    regions = []
    for key in order:
        kind, viewport = key
        regions.append(_region(buckets[key], "viewport" if kind == "viewport" else "sheet", viewport, page_index))
    if not any(region["applies_to"] == "sheet" for region in regions):
        regions.insert(0, _missing_region(page_index, "sheet", None))
        regions[0]["reason"] = (
            "No scale is printed outside the viewports. Lengths there are not measured."
        )
    return regions


def _manual_ratio(manual: dict) -> tuple[Decimal, Decimal, Decimal]:
    if manual.get("known_feet") and manual.get("distance_pt"):
        numerator = Decimal(str(manual["known_feet"]))
        denominator = Decimal(str(manual["distance_pt"]))
        return numerator / denominator, numerator, denominator
    parsed = parse_scale_text(str(manual.get("label") or ""))
    if parsed is not None and parsed.numerator is not None and parsed.denominator is not None:
        return parsed.feet_per_point, parsed.numerator, parsed.denominator
    feet = Decimal(str(manual["feet_per_point"]))
    return feet, feet, Decimal(1)


def _manual_region(manual: dict, page_index: int) -> dict:
    feet_per_point, numerator, denominator = _manual_ratio(manual)
    return {
        "page_index": page_index,
        "status": "manual",
        "source": "manual",
        "label": manual["label"],
        "feet_per_point": feet_per_point,
        "numerator": numerator,
        "denominator": denominator,
        "applies_to": "sheet",
        "viewport": None,
        "read_from": manual.get("read_from") or [],
        "reason": (
            f"Manual scale set by {manual['set_by']} at {manual['set_at']}. "
            "It overrides detection."
        ),
        "set_by": manual["set_by"],
        "set_at": manual["set_at"],
    }


def _overall(regions: list[dict]) -> tuple[str, str]:
    if len(regions) == 1:
        return regions[0]["status"], regions[0]["reason"]
    measurable = [region for region in regions if region["status"] in ("valid", "manual")]
    if measurable:
        return (
            "regional",
            "Scales are assigned per viewport. A region without one valid scale is not measured.",
        )
    return regions[0]["status"], " ".join(region["reason"] for region in regions if region["reason"])


def regions_from_words(words: list[Word], page_index: int = 0) -> list[dict]:
    """Scale regions from words alone. Graphic bars are read from the page drawing."""

    return _build_regions(_text_readings(words, page_index), [], page_index)


def located_scale_readings(page, page_index: int) -> list[dict]:
    """Scales that were actually read, each with a point on the sheet.

    A low-confidence OCR reading is withheld, the same way a scale value is.
    """

    found = []
    readings = _text_readings(page_words(page), page_index) + _graphic_bars(page, page_index)
    for reading in readings:
        feet = reading.get("feet_per_point")
        if feet is None or reading.get("conflict") or reading.get("ocr_check"):
            continue
        bbox = reading.get("bbox") or (0, 0, 0, 0)
        found.append(
            {
                "label": reading.get("label") or "",
                "kind": reading.get("kind") or "",
                "feet_per_point": feet,
                "x": round((float(bbox[0]) + float(bbox[2])) / 2, 2),
                "y": round((float(bbox[1]) + float(bbox[3])) / 2, 2),
            }
        )
    return found


def assess_page(page, page_index: int, manual: dict | None = None) -> dict:
    words = page_words(page)
    readings = _text_readings(words, page_index) + _graphic_bars(page, page_index)
    detected = _build_regions(readings, _viewport_rects(page), page_index)
    active = [_manual_region(manual, page_index)] if manual else detected
    status, reason = _overall(active)
    return {
        "page_index": page_index,
        "sheet": sheet_name(words, page_index),
        "sheet_title": read_sheet_title(words),
        "status": status,
        "reason": reason,
        "regions": active,
        "detected": detected,
        "overridden": bool(manual),
    }


def assess_pdf(pdf_bytes: bytes, manuals: dict | None = None) -> list[dict]:
    manuals = manuals or {}
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        sheets = []
        for index, page in enumerate(document):
            manual = manuals.get(index)
            if manual is None:
                manual = manuals.get(str(index))
            sheets.append(assess_page(page, index, manual))
        return sheets
    finally:
        document.close()


def region_at(sheet: dict, x: Decimal, y: Decimal) -> dict | None:
    """Return the scale region that covers one point. Manual covers the page."""

    regions = sheet["regions"]
    if any(region["status"] == "manual" for region in regions):
        return next(region for region in regions if region["status"] == "manual")
    containing = []
    for region in regions:
        viewport = region.get("viewport")
        if not viewport:
            continue
        if viewport[0] <= float(x) <= viewport[2] and viewport[1] <= float(y) <= viewport[3]:
            containing.append(region)
    if containing:
        return min(containing, key=lambda region: (region["viewport"][2] - region["viewport"][0]) * (region["viewport"][3] - region["viewport"][1]))
    for region in regions:
        if region["applies_to"] == "sheet":
            return region
    return None


def measure_polyline(sheet: dict, points: list[tuple[Decimal, Decimal]]) -> dict:
    """Measure in feet, or refuse with a reason. A refusal has no length."""

    if len(points) < 2:
        return {"ok": False, "reason": "A measured length needs at least two points."}
    covered = []
    for x_value, y_value in points:
        region = region_at(sheet, x_value, y_value)
        if region is None or region["status"] not in ("valid", "manual"):
            reason = region["reason"] if region else "No scale covers this point. Lengths are not measured."
            return {"ok": False, "reason": reason}
        covered.append(region)
    identities = {
        (region["status"], region["feet_per_point"], tuple(region["viewport"] or []))
        for region in covered
    }
    if len(identities) > 1:
        return {
            "ok": False,
            "reason": "The polyline crosses more than one scale. Lengths are not measured.",
        }
    region = covered[0]
    length = polyline_length(
        points,
        numerator=region.get("numerator"),
        denominator=region.get("denominator"),
    )
    if length <= 0:
        return {"ok": False, "reason": "The polyline length is zero. Nothing was stored."}
    read_from = "; ".join(
        f"{item['kind']} {_format_bbox(item['bbox'])}" for item in region.get("read_from") or []
    )
    return {
        "ok": True,
        "length_ft": length,
        "scale_label": region["label"],
        "scale_source": region["source"],
        "scale_read_from": read_from,
        "reason": region["reason"],
    }


def report_for_job(job: dict) -> dict:
    manuals: dict = {}
    measurements: list = []
    data_dir = data_dir_for_job(job)
    if data_dir is not None and job.get("id"):
        document = load_scales(scale_path(data_dir, str(job["id"])))
        manuals = document["manual"]
        measurements = document["measurements"]
    raw = job.get("pdf_path") or ""
    path = Path(raw) if raw else None
    if path is None or not path.is_file():
        return {"sheets": [], "measurements": measurements}
    return {
        "sheets": assess_pdf(path.read_bytes(), manuals),
        "measurements": measurements,
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def picked_scale(label: str) -> ParsedScale:
    parsed = parse_scale_text(label)
    if parsed is None or parsed.nts or parsed.conflict or parsed.feet_per_point is None:
        raise ScaleInputError("Pick a scale such as 1/8\" = 1'-0\" or 1:100.")
    return parsed


def calibrate(x1, y1, x2, y2, known_feet: Decimal) -> tuple[Decimal, str]:
    if known_feet <= 0:
        raise ScaleInputError("The known dimension must be greater than zero.")
    start = (x1, y1)
    end = (x2, y2)
    if start[0] == end[0] and start[1] == end[1]:
        raise ScaleInputError("The two points are the same, so no scale was set.")
    distance = polyline_length([start, end], Decimal(1))
    if distance < 1:
        raise ScaleInputError("The two points are too close to set a scale.")
    feet_per_point = known_feet / distance
    label = (
        f"manual, {format(known_feet, 'f')} ft between "
        f"({format(x1, 'f')},{format(y1, 'f')}) and ({format(x2, 'f')},{format(y2, 'f')})"
    )
    return feet_per_point, label
