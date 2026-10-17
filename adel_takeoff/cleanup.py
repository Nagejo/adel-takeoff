"""Drop background geometry before symbol detection.

Title-block rules, borders, revision clouds, hatching, and faint strokes
can be left out of the symbol clusters. The PDF itself is unchanged, so
markups still draw on the original sheet. Cleanup stays off unless the
shipped benchmarks gain precision without losing recall.
"""

from __future__ import annotations

import math
from pathlib import Path

from adel_takeoff.extract import Primitive


# The synthetic benchmarks do not get more precise when cleanup is on, and
# recall stays the same. Leave the filter off until a shipped set improves.
BACKGROUND_CLEANUP = False

FAINT_LEVEL = 0.72
_CLOUD_SPAN = 18.0
_CLOUD_COUNT = 8
_HATCH_COUNT = 6


def suppress_background(
    primitives: list[Primitive],
    width: float,
    height: float,
) -> list[Primitive]:
    """Return the primitives symbol detection should see."""

    drop = set()
    drop.update(_border_indexes(primitives, width, height))
    drop.update(_title_band_indexes(primitives, width, height))
    drop.update(_faint_indexes(primitives))
    drop.update(_thin_line_indexes(primitives))
    drop.update(_hatch_indexes(primitives))
    drop.update(_cloud_indexes(primitives))
    return [primitive for index, primitive in enumerate(primitives) if index not in drop]


def measure_cleanup(set_dirs: list[Path]) -> dict:
    """Compare cleanup on and off. Improved means precision rose and recall held."""

    from adel_takeoff.benchmark import score_set

    rows = []
    for folder in set_dirs:
        off = score_set(folder, cleanup=False)
        on = score_set(folder, cleanup=True)
        rows.append(
            {
                "set": Path(folder).name,
                "precision_off": off.precision,
                "precision_on": on.precision,
                "recall_off": off.recall,
                "recall_on": on.recall,
            }
        )
    improved = bool(rows) and all(
        row["recall_on"] + 1e-9 >= row["recall_off"] and row["precision_on"] + 1e-9 >= row["precision_off"]
        for row in rows
    ) and any(row["precision_on"] > row["precision_off"] + 1e-9 for row in rows)
    return {"sets": rows, "improved": improved, "enabled": BACKGROUND_CLEANUP}


def _span(bbox: tuple[float, float, float, float]) -> float:
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def _length(primitive: Primitive) -> float:
    if len(primitive.points) < 2:
        return 0.0
    start = primitive.points[0]
    end = primitive.points[1]
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _faint(primitive: Primitive) -> bool:
    color = getattr(primitive, "color", None)
    if not color or len(color) < 3:
        return False
    red, green, blue = float(color[0]), float(color[1]), float(color[2])
    return max(red, green, blue) >= FAINT_LEVEL and (max(red, green, blue) - min(red, green, blue)) <= 0.15


def _faint_indexes(primitives: list[Primitive]) -> set[int]:
    return {index for index, primitive in enumerate(primitives) if _faint(primitive)}


def _thin_line_indexes(primitives: list[Primitive]) -> set[int]:
    found = set()
    for index, primitive in enumerate(primitives):
        width = float(getattr(primitive, "width", 1.0) or 1.0)
        if width < 0.2 and _span(primitive.bbox) > 80:
            found.add(index)
    return found


def _border_indexes(primitives: list[Primitive], width: float, height: float) -> set[int]:
    found = set()
    for index, primitive in enumerate(primitives):
        if primitive.kind != "l" or len(primitive.points) < 2:
            continue
        x0, y0 = primitive.points[0]
        x1, y1 = primitive.points[1]
        length = math.hypot(x1 - x0, y1 - y0)
        horizontal = abs(y1 - y0) <= 3 and length >= 0.7 * width
        vertical = abs(x1 - x0) <= 3 and length >= 0.7 * height
        if horizontal and (min(y0, y1) <= 36 or max(y0, y1) >= height - 36):
            found.add(index)
        if vertical and (min(x0, x1) <= 36 or max(x0, x1) >= width - 36):
            found.add(index)
    return found


def _title_band_indexes(primitives: list[Primitive], width: float, height: float) -> set[int]:
    """Long strokes in the outer band. Symbol-sized marks stay."""

    found = set()
    for index, primitive in enumerate(primitives):
        if _span(primitive.bbox) < 36:
            continue
        x0, y0, x1, y1 = primitive.bbox
        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) / 2
        if center_y < height * 0.08 or center_y > height * 0.92 or center_x > width * 0.92:
            found.add(index)
    return found


def _hatch_indexes(primitives: list[Primitive]) -> set[int]:
    grouped: dict[int, list[tuple[float, int]]] = {}
    for index, primitive in enumerate(primitives):
        if primitive.kind != "l" or len(primitive.points) < 2:
            continue
        x0, y0 = primitive.points[0]
        x1, y1 = primitive.points[1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 6 or length > 40:
            continue
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180
        bucket = int(round(angle / 10.0) * 10) % 180
        normal = math.radians(bucket + 90)
        offset = ((x0 + x1) / 2) * math.cos(normal) + ((y0 + y1) / 2) * math.sin(normal)
        grouped.setdefault(bucket, []).append((offset, index))
    drop: set[int] = set()
    for entries in grouped.values():
        entries.sort()
        run = [entries[0]]

        def flush(current: list[tuple[float, int]]) -> None:
            if len(current) >= _HATCH_COUNT:
                drop.update(index for _offset, index in current)

        for offset, index in entries[1:]:
            if offset - run[-1][0] <= 8:
                run.append((offset, index))
            else:
                flush(run)
                run = [(offset, index)]
        flush(run)
    return drop


def _cloud_indexes(primitives: list[Primitive]) -> set[int]:
    indexes = [
        index
        for index, primitive in enumerate(primitives)
        if primitive.kind == "c" and _span(primitive.bbox) <= _CLOUD_SPAN
    ]
    if len(indexes) < _CLOUD_COUNT:
        return set()
    parent = {index: index for index in indexes}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(indexes)):
        for right in range(left + 1, len(indexes)):
            if _near(primitives[indexes[left]].bbox, primitives[indexes[right]].bbox, 6):
                parent[find(indexes[right])] = find(indexes[left])
    groups: dict[int, list[int]] = {}
    for index in indexes:
        groups.setdefault(find(index), []).append(index)
    drop: set[int] = set()
    for members in groups.values():
        if len(members) >= _CLOUD_COUNT:
            drop.update(members)
    return drop


def _near(a: tuple[float, float, float, float], b: tuple[float, float, float, float], pad: float) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])
