"""Company symbol library and search by example.

The estimator boxes one symbol or picks one from the library. Matches are a
silhouette comparison under rotation and mirroring. Each match is a traceable
item. A score below the high confidence cutoff stays in review until someone
accepts it.

Starter symbols are drawn here. No vendor symbol artwork is shipped.
"""

from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from adel_takeoff.classify import classify_label
from adel_takeoff.confidence import confidence_band
from adel_takeoff.constants import CONFIDENCE_HIGH_MIN
from adel_takeoff.db import insert_manual_item, list_items, update_symbol_item
from adel_takeoff.extract import (
    Primitive,
    _cluster_symbols,
    _drawings_to_primitives,
    legend_entries,
    read_pdf,
)
from adel_takeoff.links import grid_for_bbox
from adel_takeoff.review_store import next_item_id


GRID = 48
ROTATIONS = (0, 45, 90, 135, 180, 225, 270, 315)
DEFAULT_CUTOFF = 0.75
MAX_BOX_PT = 72.0
REVIEW_NOTE = (
    "Example match is below the high confidence cutoff. Review it before it counts."
)
CATEGORIES = (
    "power",
    "lighting",
    "landscape_lighting",
    "lighting_control",
    "low_voltage",
    "fire_alarm",
    "controls",
    "other",
)
_SLUG = re.compile(r"[^a-z0-9]+")
_STARTERS: list[LibrarySymbol] | None = None


@dataclass
class LibrarySymbol:
    id: str
    name: str
    category: str
    tag: str
    assembly_id: str
    origin: str
    primitives: list[Primitive] = field(default_factory=list)
    raster: tuple[tuple[int, ...], ...] | None = None

    def grid(self) -> tuple[tuple[int, ...], ...]:
        if self.primitives:
            return rasterize(self.primitives)
        if self.raster:
            return self.raster
        return _blank()

    def png(self) -> bytes:
        return grid_png(self.grid())


@dataclass(frozen=True)
class SymbolHit:
    page_index: int
    sheet: str
    sheet_title: str
    bbox: tuple[float, float, float, float]
    score: float
    transform: str
    grid: str


def company_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "symbols"


def parse_cutoff(value: str) -> float:
    try:
        cutoff = float(str(value).strip())
    except ValueError as exc:
        raise ValueError("Similarity cutoff must be a number from 0 to 1.") from exc
    if not math.isfinite(cutoff) or cutoff < 0 or cutoff > 1:
        raise ValueError("Similarity cutoff must be a number from 0 to 1.")
    return cutoff


def review_status_for(score: float) -> str:
    """Accept only a high score. Everything else waits in review."""

    if score >= CONFIDENCE_HIGH_MIN:
        return "accepted"
    return "pending"


def starter_symbols() -> list[LibrarySymbol]:
    global _STARTERS
    if _STARTERS is None:
        _STARTERS = [
            _drawn_symbol(
                "starter-duplex",
                "Duplex receptacle",
                "power",
                "DUPLEX RECEPTACLE",
                "duplex",
                _draw_duplex,
            ),
            _drawn_symbol(
                "starter-switch",
                "Single pole switch",
                "power",
                "SINGLE POLE SWITCH",
                "switch",
                _draw_switch,
            ),
            _drawn_symbol(
                "starter-recessed",
                "Recessed light",
                "lighting",
                "RECESSED LIGHT",
                "recessed-fixture",
                _draw_recessed,
            ),
        ]
    return list(_STARTERS)


def list_symbols(data_dir: Path) -> list[LibrarySymbol]:
    symbols = starter_symbols()
    known = {symbol.id for symbol in symbols}
    folder = company_dir(data_dir)
    if not folder.is_dir():
        return symbols
    for path in sorted(folder.glob("*.json")):
        loaded = _read_symbol(path)
        if loaded is None or loaded.id in known:
            continue
        symbols.append(loaded)
    return symbols


def get_symbol(data_dir: Path, symbol_id: str) -> LibrarySymbol | None:
    for symbol in list_symbols(data_dir):
        if symbol.id == symbol_id:
            return symbol
    return None


def symbol_png(data_dir: Path, symbol_id: str) -> bytes | None:
    path = company_dir(data_dir) / f"{symbol_id}.png"
    if path.is_file():
        return path.read_bytes()
    symbol = get_symbol(data_dir, symbol_id)
    if symbol is None:
        return None
    return symbol.png()


def add_symbol(
    data_dir: Path,
    *,
    name: str,
    category: str,
    tag: str,
    assembly_id: str,
    primitives: list[Primitive] | None = None,
    raster: tuple[tuple[int, ...], ...] | None = None,
) -> LibrarySymbol:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("A library symbol needs a name.")
    symbol = LibrarySymbol(
        id=_fresh_id(data_dir, cleaned),
        name=cleaned,
        category=_category(category, tag or cleaned),
        tag=" ".join((tag or cleaned).split()),
        assembly_id=assembly_id.strip(),
        origin="company",
        primitives=list(primitives or []),
        raster=raster,
    )
    if not symbol.primitives and not _ink(symbol.grid()):
        raise ValueError("No symbol was found in that box.")
    folder = company_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": symbol.id,
        "name": symbol.name,
        "category": symbol.category,
        "tag": symbol.tag,
        "assembly_id": symbol.assembly_id,
        "origin": "company",
        "primitives": _primitives_json(symbol.primitives),
        "raster": ["".join(str(cell) for cell in row) for row in symbol.raster] if symbol.raster else [],
    }
    path = folder / f"{symbol.id}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    png_path = folder / f"{symbol.id}.png"
    png_path.write_bytes(symbol.png())
    return symbol


def add_from_box(
    data_dir: Path,
    pdf_bytes: bytes,
    *,
    page_index: int,
    box: tuple[float, float, float, float],
    name: str,
    category: str,
    tag: str,
    assembly_id: str,
) -> LibrarySymbol:
    primitives, raster = template_from_box(pdf_bytes, page_index, box)
    return add_symbol(
        data_dir,
        name=name,
        category=category,
        tag=tag,
        assembly_id=assembly_id,
        primitives=primitives,
        raster=raster,
    )


def template_from_box(
    pdf_bytes: bytes,
    page_index: int,
    box: tuple[float, float, float, float],
) -> tuple[list[Primitive], tuple[tuple[int, ...], ...] | None]:
    _check_box(box)
    pages = read_pdf(pdf_bytes)
    if page_index < 0 or page_index >= len(pages):
        raise ValueError("That page is not in this set.")
    page = pages[page_index]
    chosen = [
        cluster
        for cluster in page.clusters
        if box[0] <= cluster.center[0] <= box[2] and box[1] <= cluster.center[1] <= box[3]
    ]
    if len(chosen) > 1:
        raise ValueError("Box one symbol.")
    if len(chosen) == 1:
        return list(chosen[0].primitives), None
    raster = _raster_crop(pdf_bytes, page_index, box)
    if not _ink(raster):
        raise ValueError("No symbol was found in that box.")
    return [], raster


def find_matches(
    pdf_bytes: bytes,
    primitives: list[Primitive],
    raster: tuple[tuple[int, ...], ...] | None,
    cutoff: float,
) -> list[SymbolHit]:
    """Return plan marks at least as similar as ``cutoff``.

    Legend rows are not matches. Rotation is 45 degrees and finer, and each
    rotation is also tried mirrored.
    """

    if cutoff < 0 or cutoff > 1:
        raise ValueError("Similarity cutoff must be a number from 0 to 1.")
    variants = _variants(primitives, raster)
    if not variants or not any(_ink(grid) for _name, grid in variants):
        return []
    pages = read_pdf(pdf_bytes)
    hits: list[SymbolHit] = []
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in pages:
            legend_boxes = {entry.bbox for entry in legend_entries(page)}
            if page.clusters:
                for cluster in page.clusters:
                    if cluster.bbox in legend_boxes:
                        continue
                    score, transform = _best(variants, rasterize(cluster.primitives))
                    if score + 1e-9 < cutoff:
                        continue
                    hits.append(
                        SymbolHit(
                            page_index=page.index,
                            sheet=page.sheet,
                            sheet_title=page.sheet_title,
                            bbox=cluster.bbox,
                            score=round(score, 4),
                            transform=transform,
                            grid=grid_for_bbox(cluster.bbox, page.words),
                        )
                    )
                continue
            hits.extend(
                _scan_raster(
                    document[page.index],
                    page,
                    primitives,
                    raster,
                    cutoff,
                )
            )
    finally:
        document.close()
    return hits


def similarity(left: list[Primitive], right: list[Primitive]) -> tuple[float, str]:
    """Best score of ``left`` against ``right`` over rotation and mirror."""

    return _best(_variants(left, None), rasterize(right))


def apply_matches(
    conn,
    job_id: str,
    pdf_bytes: bytes,
    symbol: LibrarySymbol,
    cutoff: float,
) -> dict:
    """Store every match as a traceable item.

    A mark the legend already counted is left as it is. An unlegended mark
    takes on this symbol. A new spot is inserted. Scores below the high
    cutoff are pending and do not count until review accepts them.
    """

    hits = find_matches(pdf_bytes, symbol.primitives, symbol.raster, cutoff)
    items = list_items(conn, job_id)
    inserted = 0
    updated = 0
    kept = 0
    for hit in sorted(hits, key=lambda item: (-item.score, item.page_index, item.bbox)):
        overlap = _overlapping(items, hit)
        status = review_status_for(hit.score)
        note = "" if status == "accepted" else REVIEW_NOTE
        fields = _item_fields(symbol, hit, status, note)
        if overlap is None:
            fields["id"] = _allocate_id(conn, items)
            insert_manual_item(conn, job_id, fields)
            items.append(fields)
            inserted += 1
            continue
        if overlap.get("method") == "unmatched":
            update_symbol_item(conn, job_id, str(overlap["id"]), fields)
            overlap.update(fields)
            overlap["method"] = "symbol_search"
            updated += 1
            continue
        kept += 1
    return {
        "hits": len(hits),
        "inserted": inserted,
        "updated": updated,
        "kept": kept,
        "cutoff": cutoff,
    }


def describe_matches(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        if item.get("method") != "symbol_search":
            continue
        score = item.get("match_score")
        try:
            parsed = None if score in (None, "") else float(score)
        except (TypeError, ValueError):
            parsed = None
        occurrence = (item.get("occurrences") or [{}])[0]
        rows.append(
            {
                "id": str(item["id"]),
                "label": item.get("label") or "",
                "sheet": (item.get("sheets") or [""])[0],
                "location": _location(occurrence),
                "score": "" if parsed is None else f"{parsed:.2f}",
                "confidence": confidence_band(parsed),
                "review_status": item.get("review_status") or "pending",
                "transform": "",
            }
        )
    return rows


def rasterize(primitives: list[Primitive], size: int = GRID) -> tuple[tuple[int, ...], ...]:
    grid = [[0 for _ in range(size)] for _ in range(size)]
    points = [point for primitive in primitives for point in primitive.points]
    if len(points) < 2:
        return _freeze(grid)
    min_x, min_y, max_x, max_y = _bbox_of(points)
    span = max(max_x - min_x, max_y - min_y) or 1.0
    pad = 2
    usable = size - 1 - 2 * pad

    def place(x: float, y: float) -> tuple[float, float]:
        extra_x = (span - (max_x - min_x)) / 2 / span * usable
        extra_y = (span - (max_y - min_y)) / 2 / span * usable
        return (
            pad + (x - min_x) / span * usable + extra_x,
            pad + (y - min_y) / span * usable + extra_y,
        )

    for primitive in primitives:
        drawn = [place(x, y) for x, y in primitive.points]
        if primitive.kind == "c" and len(drawn) >= 4:
            _stroke(grid, _bezier(drawn[0], drawn[1], drawn[2], drawn[3]))
        elif primitive.kind in {"re", "qu"} and len(drawn) >= 2:
            closed = drawn + [drawn[0]]
            for start, end in zip(closed, closed[1:]):
                _line(grid, start[0], start[1], end[0], end[1])
        else:
            for start, end in zip(drawn, drawn[1:]):
                _line(grid, start[0], start[1], end[0], end[1])
    return _freeze(grid)


def grid_png(grid: tuple[tuple[int, ...], ...] | list, scale: int = 4) -> bytes:
    from PIL import Image

    height = len(grid)
    width = len(grid[0]) if height else 0
    image = Image.new("RGB", (max(1, width * scale), max(1, height * scale)), "white")
    pixels = image.load()
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            if not cell:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    pixels[x * scale + dx, y * scale + dy] = (28, 28, 28)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _variants(primitives: list[Primitive], raster) -> list[tuple[str, tuple]]:
    variants = []
    if primitives:
        for degrees in ROTATIONS:
            rotated = _rotate(primitives, degrees)
            variants.append((f"{degrees}", rasterize(rotated)))
            variants.append((f"{degrees} mirror", rasterize(_mirror(rotated))))
        return variants
    base = raster or _blank()
    for degrees in ROTATIONS:
        rotated = _rotate_grid(base, degrees)
        variants.append((f"{degrees}", rotated))
        variants.append((f"{degrees} mirror", _mirror_grid(rotated)))
    return variants


def _best(variants: list[tuple[str, tuple]], candidate: tuple) -> tuple[float, str]:
    thick = _dilate(candidate)
    best = 0.0
    name = ""
    for label, grid in variants:
        score = _iou(_dilate(grid), thick)
        if score > best:
            best = score
            name = label
    return best, name


def _iou(left, right) -> float:
    overlap = 0
    union = 0
    for row_left, row_right in zip(left, right):
        for cell_left, cell_right in zip(row_left, row_right):
            if cell_left or cell_right:
                union += 1
            if cell_left and cell_right:
                overlap += 1
    if union == 0:
        return 0.0
    return overlap / union


def _dilate(grid):
    height = len(grid)
    width = len(grid[0]) if height else 0
    out = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if not grid[y][x]:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy = y + dy
                    xx = x + dx
                    if 0 <= yy < height and 0 <= xx < width:
                        out[yy][xx] = 1
    return _freeze(out)


def _rotate(primitives: list[Primitive], degrees: float) -> list[Primitive]:
    if degrees % 360 == 0 or not primitives:
        return primitives
    cx, cy = _center(primitives)
    radians = math.radians(degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)

    def turn(x: float, y: float) -> tuple[float, float]:
        dx = x - cx
        dy = y - cy
        return (cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)

    return _map(primitives, turn)


def _mirror(primitives: list[Primitive]) -> list[Primitive]:
    if not primitives:
        return primitives
    cx, _cy = _center(primitives)

    def flip(x: float, y: float) -> tuple[float, float]:
        return (cx - (x - cx), y)

    return _map(primitives, flip)


def _map(primitives: list[Primitive], fn) -> list[Primitive]:
    mapped = []
    for primitive in primitives:
        points = [fn(x, y) for x, y in primitive.points]
        mapped.append(Primitive(primitive.kind, points, _bbox_of(points)))
    return mapped


def _center(primitives: list[Primitive]) -> tuple[float, float]:
    points = [point for primitive in primitives for point in primitive.points]
    min_x, min_y, max_x, max_y = _bbox_of(points)
    return ((min_x + max_x) / 2, (min_y + max_y) / 2)


def _rotate_grid(grid, degrees: float):
    if degrees % 360 == 0:
        return grid
    size = len(grid)
    out = [[0 for _ in range(size)] for _ in range(size)]
    center = (size - 1) / 2
    radians = math.radians(-degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            sx = int(round(dx * cos_a - dy * sin_a + center))
            sy = int(round(dx * sin_a + dy * cos_a + center))
            if 0 <= sx < size and 0 <= sy < size and grid[sy][sx]:
                out[y][x] = 1
    return _freeze(out)


def _mirror_grid(grid):
    return tuple(tuple(reversed(row)) for row in grid)


def _blank() -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(0 for _ in range(GRID)) for _ in range(GRID))


def _freeze(grid) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(cell) for cell in row) for row in grid)


def _ink(grid) -> int:
    return sum(cell for row in grid for cell in row)


def _stroke(grid, points: list[tuple[float, float]]) -> None:
    for start, end in zip(points, points[1:]):
        _line(grid, start[0], start[1], end[0], end[1])


def _bezier(p0, p1, p2, p3, steps: int = 12) -> list[tuple[float, float]]:
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append(
            (
                u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return points


def _line(grid, x0: float, y0: float, x1: float, y1: float) -> None:
    ax = int(round(x0))
    ay = int(round(y0))
    bx = int(round(x1))
    by = int(round(y1))
    dx = abs(bx - ax)
    dy = -abs(by - ay)
    sx = 1 if ax < bx else -1
    sy = 1 if ay < by else -1
    err = dx + dy
    height = len(grid)
    width = len(grid[0]) if height else 0
    while True:
        if 0 <= ax < width and 0 <= ay < height:
            grid[ay][ax] = 1
        if ax == bx and ay == by:
            break
        doubled = 2 * err
        if doubled >= dy:
            err += dy
            ax += sx
        if doubled <= dx:
            err += dx
            ay += sy


def _item_fields(symbol: LibrarySymbol, hit: SymbolHit, status: str, note: str) -> dict:
    x0, y0, x1, y1 = hit.bbox
    return {
        "label": symbol.tag or symbol.name,
        "category": symbol.category,
        "measurement": "matched",
        "measured_quantity": 1,
        "sheets": [hit.sheet] if hit.sheet else [],
        "panel": "",
        "circuit": "",
        "link_flag": note,
        "occurrences": [
            {
                "page_index": hit.page_index,
                "sheet": hit.sheet,
                "sheet_title": hit.sheet_title,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "grid": hit.grid,
            }
        ],
        "method": "symbol_search",
        "match_score": hit.score,
        "review_status": status,
        "exclusion": "",
        "symbol_assembly": symbol.assembly_id,
    }


def _overlapping(items: list[dict], hit: SymbolHit) -> dict | None:
    cx = (hit.bbox[0] + hit.bbox[2]) / 2
    cy = (hit.bbox[1] + hit.bbox[3]) / 2
    for item in items:
        for occurrence in item.get("occurrences") or []:
            if int(occurrence.get("page_index", -1)) != hit.page_index:
                continue
            if (
                float(occurrence["x0"]) - 4 <= cx <= float(occurrence["x1"]) + 4
                and float(occurrence["y0"]) - 4 <= cy <= float(occurrence["y1"]) + 4
            ):
                return item
    return None


def _allocate_id(conn, items: list[dict]) -> str:
    taken = {str(item["id"]) for item in items}
    rows = conn.execute("SELECT id FROM items").fetchall()
    taken.update(str(row["id"]) for row in rows)
    number = int(next_item_id(list(taken)))
    while str(number) in taken:
        number += 1
    return str(number)


def _location(occurrence: dict) -> str:
    if not occurrence:
        return ""
    x0 = float(occurrence.get("x0") or 0)
    y0 = float(occurrence.get("y0") or 0)
    x1 = float(occurrence.get("x1") or 0)
    y1 = float(occurrence.get("y1") or 0)
    grid = occurrence.get("grid") or ""
    point = f"({(x0 + x1) / 2:.1f}, {(y0 + y1) / 2:.1f}) pt"
    if grid:
        return f"grid {grid}; {point}"
    return point


def _category(category: str, tag: str) -> str:
    cleaned = category.strip()
    if cleaned in CATEGORIES:
        return cleaned
    return classify_label(tag).category


def _fresh_id(data_dir: Path, name: str) -> str:
    base = _SLUG.sub("-", name.lower()).strip("-") or "symbol"
    if base.startswith("starter-"):
        base = base[len("starter-") :]
    taken = {symbol.id for symbol in list_symbols(data_dir)}
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def _read_symbol(path: Path) -> LibrarySymbol | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id") or not data.get("name"):
        return None
    primitives = _primitives_from(data.get("primitives") or [])
    raster = _raster_from(data.get("raster") or [])
    return LibrarySymbol(
        id=str(data["id"]),
        name=str(data["name"]),
        category=_category(str(data.get("category") or ""), str(data.get("tag") or data["name"])),
        tag=str(data.get("tag") or data["name"]),
        assembly_id=str(data.get("assembly_id") or ""),
        origin="company",
        primitives=primitives,
        raster=raster,
    )


def _primitives_json(primitives: list[Primitive]) -> list[dict]:
    return [
        {
            "kind": primitive.kind,
            "points": [[round(x, 3), round(y, 3)] for x, y in primitive.points],
        }
        for primitive in primitives
    ]


def _primitives_from(records) -> list[Primitive]:
    primitives = []
    for record in records:
        if not isinstance(record, dict):
            continue
        points = []
        for point in record.get("points") or []:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                points.append((float(point[0]), float(point[1])))
        if len(points) < 2:
            continue
        primitives.append(Primitive(str(record.get("kind") or "l"), points, _bbox_of(points)))
    return primitives


def _raster_from(rows) -> tuple[tuple[int, ...], ...] | None:
    if not rows:
        return None
    grid = []
    for row in rows:
        text = str(row)
        grid.append(tuple(1 if cell == "1" else 0 for cell in text))
    width = len(grid[0])
    if width == 0 or any(len(row) != width for row in grid):
        return None
    return tuple(grid)


def _check_box(box: tuple[float, float, float, float]) -> None:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The box needs a lower-left and an upper-right.")
    if max(x1 - x0, y1 - y0) > MAX_BOX_PT:
        raise ValueError("Box one symbol. This region is too large.")


def _template_span(primitives: list[Primitive], raster) -> float:
    if primitives:
        points = [point for primitive in primitives for point in primitive.points]
        if points:
            min_x, min_y, max_x, max_y = _bbox_of(points)
            return max(max_x - min_x, max_y - min_y, 8.0)
    return 16.0


def _raster_crop(pdf_bytes: bytes, page_index: int, box: tuple[float, float, float, float]):
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = document[page_index]
        span = max(box[2] - box[0], box[3] - box[1], 8.0)
        scale = (GRID - 4) / span
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=fitz.Rect(*box),
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        return _resample(_binary_pixmap(pixmap), GRID)
    finally:
        document.close()


def _scan_raster(page, page_data, primitives, raster, cutoff: float) -> list[SymbolHit]:
    """Slide a rendered silhouette over a page that has no vector marks."""

    scale = 2.0
    templates = _pixel_templates(primitives, raster, scale)
    if not templates:
        return []
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    binary = _binary_pixmap(pixmap)
    height = len(binary)
    width = len(binary[0]) if height else 0
    if width < 4 or height < 4:
        return []
    # A one-pixel halo keeps a rotated scan mark from missing on a two-pixel step.
    thick = [list(row) for row in _dilate(binary)]
    integral = _integral(binary)
    found: list[tuple[float, str, int, int, int, int]] = []
    for label, grid, _span in templates:
        grid = _trim(grid)
        tw = len(grid[0]) if grid else 0
        th = len(grid)
        if tw > width or th > height or tw < 2 or th < 2:
            continue
        points = _ink_points(grid)
        ink = len(points)
        if ink < 4:
            continue
        step = 2
        for origin_y in range(1, height - th - 1, step):
            for origin_x in range(1, width - tw - 1, step):
                count = _rect_sum(integral, origin_x, origin_y, tw, th)
                if count < ink * 0.45 or count > ink * 2.2:
                    continue
                overlap = 0
                for x, y in points:
                    if thick[origin_y + y][origin_x + x]:
                        overlap += 1
                score = overlap / ink
                if score + 1e-9 >= cutoff:
                    found.append((score, label, origin_x, origin_y, tw, th))
    kept = _suppress(found)
    hits = []
    for score, transform, origin_x, origin_y, tw, th in kept:
        bbox = (
            origin_x / scale,
            origin_y / scale,
            (origin_x + tw) / scale,
            (origin_y + th) / scale,
        )
        hits.append(
            SymbolHit(
                page_index=page_data.index,
                sheet=page_data.sheet,
                sheet_title=page_data.sheet_title,
                bbox=bbox,
                score=round(min(1.0, score), 4),
                transform=transform,
                grid=grid_for_bbox(bbox, page_data.words),
            )
        )
    return hits


def _pixel_templates(primitives, raster, scale: float) -> list[tuple[str, tuple, float]]:
    if primitives:
        span = _template_span(primitives, None)
        templates = []
        for degrees in ROTATIONS:
            rotated = _rotate(primitives, degrees)
            templates.append((f"{degrees}", _render_mark(rotated, scale), span))
            templates.append((f"{degrees} mirror", _render_mark(_mirror(rotated), scale), span))
        return [(label, grid, span) for label, grid, span in templates if _ink(grid)]
    if not raster:
        return []
    templates = []
    for degrees in ROTATIONS:
        rotated = _rotate_grid(raster, degrees)
        templates.append((f"{degrees}", _trim(rotated), 16.0))
        templates.append((f"{degrees} mirror", _trim(_mirror_grid(rotated)), 16.0))
    return templates


def _render_mark(primitives: list[Primitive], scale: float) -> tuple:
    points = [point for primitive in primitives for point in primitive.points]
    if len(points) < 2:
        return _blank()
    min_x, min_y, max_x, max_y = _bbox_of(points)
    pad = 3.0
    width = max(8.0, max_x - min_x + pad * 2)
    height = max(8.0, max_y - min_y + pad * 2)
    document = fitz.open()
    try:
        page = document.new_page(width=width, height=height)
        origin_x = min_x - pad
        origin_y = min_y - pad
        for primitive in primitives:
            shifted = [(x - origin_x, y - origin_y) for x, y in primitive.points]
            _paint(page, primitive.kind, shifted)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
        return _binary_pixmap(pixmap)
    finally:
        document.close()


def _paint(page, kind: str, points: list[tuple[float, float]]) -> None:
    if kind == "l" and len(points) >= 2:
        page.draw_line(
            fitz.Point(*points[0]),
            fitz.Point(*points[1]),
            color=(0, 0, 0),
            width=0.8,
        )
    elif kind == "re" and len(points) >= 2:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        page.draw_rect(
            fitz.Rect(min(xs), min(ys), max(xs), max(ys)),
            color=(0, 0, 0),
            width=0.8,
        )
    elif kind == "c" and len(points) >= 4:
        page.draw_bezier(
            fitz.Point(*points[0]),
            fitz.Point(*points[1]),
            fitz.Point(*points[2]),
            fitz.Point(*points[3]),
            color=(0, 0, 0),
            width=0.8,
        )
    else:
        for start, end in zip(points, points[1:] + points[:1]):
            page.draw_line(fitz.Point(*start), fitz.Point(*end), color=(0, 0, 0), width=0.8)


def _trim(grid) -> tuple:
    rows = [index for index, row in enumerate(grid) if any(row)]
    cols = [index for index in range(len(grid[0])) if any(row[index] for row in grid)]
    if not rows or not cols:
        return grid
    y0, y1 = rows[0], rows[-1]
    x0, x1 = cols[0], cols[-1]
    return tuple(tuple(row[x0 : x1 + 1]) for row in grid[y0 : y1 + 1])


def _integral(grid: list[list[int]]):
    height = len(grid)
    width = len(grid[0]) if height else 0
    integral = [[0 for _ in range(width + 1)] for _ in range(height + 1)]
    for y in range(height):
        running = 0
        for x in range(width):
            running += grid[y][x]
            integral[y + 1][x + 1] = integral[y][x + 1] + running
    return integral


def _rect_sum(integral, x: int, y: int, width: int, height: int) -> int:
    return (
        integral[y + height][x + width]
        - integral[y][x + width]
        - integral[y + height][x]
        + integral[y][x]
    )


def _suppress(found: list[tuple]) -> list[tuple]:
    ordered = sorted(found, key=lambda item: -item[0])
    kept = []
    for candidate in ordered:
        _score, _transform, x, y = candidate[:4]
        if any(abs(x - other[2]) < 12 and abs(y - other[3]) < 12 for other in kept):
            continue
        kept.append(candidate)
    return kept


def _ink_points(grid) -> list[tuple[int, int]]:
    return [
        (x, y)
        for y, row in enumerate(grid)
        for x, cell in enumerate(row)
        if cell
    ]


def _binary_pixmap(pixmap) -> list[list[int]]:
    samples = pixmap.samples
    width = pixmap.width
    height = pixmap.height
    grid = []
    for y in range(height):
        start = y * width
        grid.append([1 if samples[start + x] < 180 else 0 for x in range(width)])
    return grid


def _resample(grid: list[list[int]], size: int):
    height = len(grid)
    width = len(grid[0]) if height else 0
    out = [[0 for _ in range(size)] for _ in range(size)]
    if width == 0 or height == 0:
        return _freeze(out)
    for y in range(size):
        for x in range(size):
            sx0 = int(x * width / size)
            sx1 = max(sx0 + 1, int((x + 1) * width / size))
            sy0 = int(y * height / size)
            sy1 = max(sy0 + 1, int((y + 1) * height / size))
            ink = 0
            total = 0
            for yy in range(sy0, min(sy1, height)):
                for xx in range(sx0, min(sx1, width)):
                    total += 1
                    ink += grid[yy][xx]
            if total and ink / total >= 0.15:
                out[y][x] = 1
    return _freeze(out)


def _drawn_symbol(symbol_id, name, category, tag, assembly_id, draw) -> LibrarySymbol:
    document = fitz.open()
    page = document.new_page(width=80, height=80)
    draw(page, 40, 40)
    primitives = []
    clusters = _cluster_symbols(_drawings_to_primitives(page), 0)
    if clusters:
        primitives = clusters[0].primitives
    document.close()
    return LibrarySymbol(
        id=symbol_id,
        name=name,
        category=category,
        tag=tag,
        assembly_id=assembly_id,
        origin="starter",
        primitives=primitives,
    )


def _draw_duplex(page, x, y) -> None:
    page.draw_circle(fitz.Point(x, y), 8, color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 4, y - 2), fitz.Point(x + 4, y - 2), color=(0, 0, 0), width=0.6)
    page.draw_line(fitz.Point(x - 4, y + 2), fitz.Point(x + 4, y + 2), color=(0, 0, 0), width=0.6)


def _draw_switch(page, x, y) -> None:
    page.draw_rect(fitz.Rect(x - 8, y - 8, x + 8, y + 8), color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 6, y + 6), fitz.Point(x + 6, y - 6), color=(0, 0, 0), width=0.6)


def _draw_recessed(page, x, y) -> None:
    page.draw_circle(fitz.Point(x, y), 10, color=(0, 0, 0), width=0.8)
    page.draw_line(fitz.Point(x - 7, y), fitz.Point(x + 7, y), color=(0, 0, 0), width=0.6)
    page.draw_line(fitz.Point(x, y - 7), fitz.Point(x, y + 7), color=(0, 0, 0), width=0.6)


def _bbox_of(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))
