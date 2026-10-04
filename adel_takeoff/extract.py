"""Read vector marks, legend rows, and match lines from a PDF.

Symbol identity is the normalized geometry of a small vector cluster. A mark
counts only when that identity equals a legend symbol. Position is removed.
Size, orientation, and stroke order stay part of the identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz

from adel_takeoff.constants import (
    LEGEND_ROW_GAP_PT,
    MAX_SYMBOL_PT,
    MIN_MATCH_LINE_PT,
    MIN_SYMBOL_PT,
)


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Primitive:
    kind: str
    points: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


@dataclass
class Cluster:
    page_index: int
    primitives: list[Primitive]
    bbox: tuple[float, float, float, float]
    fingerprint: tuple
    center: tuple[float, float]

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]


@dataclass(frozen=True)
class LegendEntry:
    fingerprint: tuple
    label: str
    page_index: int
    bbox: tuple[float, float, float, float]
    ambiguous: bool = False


@dataclass(frozen=True)
class MatchLine:
    page_index: int
    key: str
    depth: float | None
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PageData:
    index: int
    width: float
    height: float
    words: list[Word]
    clusters: list[Cluster] = field(default_factory=list)
    match_lines: list[MatchLine] = field(default_factory=list)
    sheet: str = ""
    text: str = ""


def _quantize(value: float) -> float:
    return round(float(value), 2)


def _as_point(value) -> tuple[float, float] | None:
    if hasattr(value, "x") and hasattr(value, "y"):
        return (float(value.x), float(value.y))
    return None


def _bbox_of(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _primitive_from_item(item) -> Primitive | None:
    kind = item[0]
    points: list[tuple[float, float]] = []
    if kind == "l":
        for value in item[1:3]:
            point = _as_point(value)
            if point is None:
                return None
            points.append(point)
    elif kind == "c":
        for value in item[1:5]:
            point = _as_point(value)
            if point is None:
                return None
            points.append(point)
    elif kind == "re":
        rect = item[1]
        points = [
            (float(rect.x0), float(rect.y0)),
            (float(rect.x1), float(rect.y0)),
            (float(rect.x1), float(rect.y1)),
            (float(rect.x0), float(rect.y1)),
        ]
    elif kind == "qu":
        quad = item[1]
        for value in (quad.ul, quad.ur, quad.ll, quad.lr):
            point = _as_point(value)
            if point is None:
                return None
            points.append(point)
    else:
        return None
    if len(points) < 2:
        return None
    return Primitive(kind=kind, points=points, bbox=_bbox_of(points))


def _drawings_to_primitives(page) -> list[Primitive]:
    primitives: list[Primitive] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            primitive = _primitive_from_item(item)
            if primitive is not None:
                primitives.append(primitive)
    return primitives


def _span(bbox: tuple[float, float, float, float]) -> float:
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def _expanded_hit(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float,
) -> bool:
    return not (
        a[2] + pad < b[0]
        or b[2] + pad < a[0]
        or a[3] + pad < b[1]
        or b[3] + pad < a[1]
    )


class _UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def _fingerprint(primitives: list[Primitive]) -> tuple:
    points = [point for primitive in primitives for point in primitive.points]
    origin_x = min(point[0] for point in points)
    origin_y = min(point[1] for point in points)
    parts = []
    for primitive in primitives:
        relative = [
            (_quantize(point[0] - origin_x), _quantize(point[1] - origin_y))
            for point in primitive.points
        ]
        if primitive.kind == "l":
            parts.append((primitive.kind, tuple(sorted(relative))))
        elif primitive.kind == "re":
            xs = [point[0] for point in relative]
            ys = [point[1] for point in relative]
            parts.append(
                (
                    "re",
                    (_quantize(min(xs)), _quantize(min(ys)), _quantize(max(xs)), _quantize(max(ys))),
                )
            )
        else:
            parts.append((primitive.kind, tuple(relative)))
    return tuple(sorted(parts))


def _cluster_symbols(primitives: list[Primitive], page_index: int) -> list[Cluster]:
    small = [primitive for primitive in primitives if _span(primitive.bbox) <= MAX_SYMBOL_PT]
    if not small:
        return []
    union = _UnionFind(len(small))
    for left in range(len(small)):
        for right in range(left + 1, len(small)):
            if _expanded_hit(small[left].bbox, small[right].bbox, 3.0):
                union.union(left, right)
    groups: dict[int, list[Primitive]] = {}
    for index, primitive in enumerate(small):
        groups.setdefault(union.find(index), []).append(primitive)
    clusters: list[Cluster] = []
    for grouped in groups.values():
        points = [point for primitive in grouped for point in primitive.points]
        bbox = _bbox_of(points)
        span = _span(bbox)
        if span < MIN_SYMBOL_PT or span > MAX_SYMBOL_PT:
            continue
        center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        clusters.append(
            Cluster(
                page_index=page_index,
                primitives=grouped,
                bbox=bbox,
                fingerprint=_fingerprint(grouped),
                center=center,
            )
        )
    return clusters


def _long_lines(primitives: list[Primitive]) -> list[tuple[float, float, float, float]]:
    lines = []
    for primitive in primitives:
        if primitive.kind != "l" or len(primitive.points) < 2:
            continue
        start, end = primitive.points[0], primitive.points[1]
        length = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
        if length >= MIN_MATCH_LINE_PT:
            lines.append((start[0], start[1], end[0], end[1]))
    return lines


def page_words(page) -> list[Word]:
    words = []
    for raw in page.get_text("words"):
        words.append(Word(float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]), str(raw[4])))
    return words


def group_lines(words: list[Word]) -> list[list[Word]]:
    ordered = sorted(words, key=lambda word: (round(word.y0, 0), word.x0))
    lines: list[list[Word]] = []
    for word in ordered:
        if lines and abs(word.y0 - lines[-1][0].y0) <= 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda word: word.x0)
    return lines


def sheet_name(words: list[Word], page_index: int) -> str:
    for line in group_lines(words):
        texts = [word.text for word in line]
        for index, text in enumerate(texts[:-1]):
            if text.upper() == "SHEET":
                return texts[index + 1].upper()
    return f"Page {page_index + 1}"


def _phrase_word(line: list[Word], phrase: tuple[str, ...]) -> int | None:
    texts = [word.text.upper() for word in line]
    size = len(phrase)
    for index in range(len(texts) - size + 1):
        if tuple(texts[index : index + size]) == phrase:
            return index
    return None


def _find_heading(words: list[Word]) -> Word | None:
    for line in group_lines(words):
        if _phrase_word(line, ("SYMBOL", "LEGEND")) is not None:
            return line[0]
    for line in group_lines(words):
        if len(line) == 1 and line[0].text.upper() == "LEGEND":
            return line[0]
    return None


def _label_for(cluster: Cluster, words: list[Word]) -> str:
    x1 = cluster.bbox[2]
    cy = cluster.center[1]
    chosen = [
        word
        for word in words
        if word.x0 >= x1 - 2
        and word.x0 <= x1 + 180
        and abs(word.cy - cy) <= 12
    ]
    chosen.sort(key=lambda word: word.x0)
    return " ".join(word.text for word in chosen).strip()


def legend_entries(page: PageData) -> list[LegendEntry]:
    """Legend rows are labeled symbol clusters stacked under the legend heading."""

    heading = _find_heading(page.words)
    if heading is None:
        return []
    candidates = []
    for cluster in page.clusters:
        if cluster.center[1] < heading.y0 - 4:
            continue
        if not (heading.x0 - 40 <= cluster.center[0] <= heading.x0 + 140):
            continue
        label = _label_for(cluster, page.words)
        if not label:
            continue
        candidates.append((cluster, label))
    candidates.sort(key=lambda item: item[0].center[1])
    if not candidates:
        return []
    rows = [candidates[0]]
    for cluster, label in candidates[1:]:
        gap = cluster.center[1] - rows[-1][0].center[1]
        if gap > LEGEND_ROW_GAP_PT:
            break
        rows.append((cluster, label))
    found: dict[tuple, LegendEntry] = {}
    order: list[tuple] = []
    for cluster, label in rows:
        fingerprint = cluster.fingerprint
        if fingerprint in found:
            previous = found[fingerprint]
            found[fingerprint] = LegendEntry(
                fingerprint=fingerprint,
                label=previous.label,
                page_index=previous.page_index,
                bbox=previous.bbox,
                ambiguous=True,
            )
            continue
        found[fingerprint] = LegendEntry(
            fingerprint=fingerprint,
            label=label,
            page_index=page.index,
            bbox=cluster.bbox,
            ambiguous=False,
        )
        order.append(fingerprint)
    return [found[fingerprint] for fingerprint in order]


def _distance_point_segment(
    px: float, py: float, x0: float, y0: float, x1: float, y1: float
) -> float:
    dx = x1 - x0
    dy = y1 - y0
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return ((px - x0) ** 2 + (py - y0) ** 2) ** 0.5
    ratio = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / length_sq))
    qx = x0 + ratio * dx
    qy = y0 + ratio * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def _match_lines_on_page(
    page_index: int, words: list[Word], lines: list[tuple[float, float, float, float]]
) -> list[MatchLine]:
    found = []
    for line_words in group_lines(words):
        start = _phrase_word(line_words, ("MATCH", "LINE"))
        if start is None:
            start = _phrase_word(line_words, ("MATCHLINE",))
            phrase_len = 1 if start is not None else 0
        else:
            phrase_len = 2
        if start is None:
            continue
        rest = line_words[start + phrase_len :]
        key = ""
        depth: float | None = None
        index = 0
        while index < len(rest):
            token = rest[index].text.upper()
            if token == "OVERLAP" and index + 1 < len(rest):
                try:
                    depth = float(rest[index + 1].text)
                except ValueError:
                    depth = None
                index += 2
                continue
            if not key:
                key = token
            index += 1
        anchor = line_words[start]
        if not lines or not key:
            continue
        nearest = min(
            lines,
            key=lambda segment: _distance_point_segment(
                anchor.cx, anchor.cy, segment[0], segment[1], segment[2], segment[3]
            ),
        )
        distance = _distance_point_segment(
            anchor.cx, anchor.cy, nearest[0], nearest[1], nearest[2], nearest[3]
        )
        if distance > 40:
            continue
        found.append(
            MatchLine(
                page_index,
                key,
                depth,
                nearest[0],
                nearest[1],
                nearest[2],
                nearest[3],
            )
        )
    return found


def read_pdf(pdf_bytes: bytes) -> list[PageData]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages: list[PageData] = []
        for index, page in enumerate(document):
            words = page_words(page)
            primitives = _drawings_to_primitives(page)
            page_data = PageData(
                index=index,
                width=float(page.rect.width),
                height=float(page.rect.height),
                words=words,
                clusters=_cluster_symbols(primitives, index),
                match_lines=_match_lines_on_page(index, words, _long_lines(primitives)),
                sheet=sheet_name(words, index),
                text=page.get_text("text"),
            )
            pages.append(page_data)
        return pages
    finally:
        document.close()
