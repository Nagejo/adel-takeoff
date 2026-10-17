"""Sheet zones for alternates, phases, floors, areas, and by-others work.

An estimator draws a rectangle or a polygon on one sheet and names it.
The same name on another sheet is the same zone. A counted mark is
assigned from the center of its own sheet coordinates. Two zones of one
type that both contain that center are a review flag, and neither zone
is applied. Nothing here reads a vendor takeoff or routes conduit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ZONE_TYPES = (
    ("alternate", "Alternate"),
    ("phase", "Phase"),
    ("floor", "Floor"),
    ("area", "Area"),
    ("by_others", "By others / NIC"),
)
ZONE_TYPE_VALUES = {key for key, _label in ZONE_TYPES}
ALTERNATE_SIDES = ("add", "deduct")
SHAPES = ("rectangle", "polygon")

_EDGE_EPSILON = 1e-6
_ZONE_ID = re.compile(r"^z(\d+)$")
_HELD = {"by_others", "alternate"}
_TYPE_NOUN = {
    "alternate": "alternate zone",
    "phase": "phase zone",
    "floor": "floor zone",
    "area": "area zone",
    "by_others": "by others / NIC zone",
}


class ZoneError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def type_label(value: str) -> str:
    for key, label in ZONE_TYPES:
        if key == value:
            return label
    return value


def zones_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "zones" / f"{job_id}.json"


def load_zones(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    raw = data.get("zones") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    zones = []
    for item in raw:
        zone = _stored_zone(item)
        if zone is not None:
            zones.append(zone)
    return _sorted(zones)


def save_zones(path: Path, zones: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"zones": _sorted(zones)}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def add_zone(path: Path, spec: dict, sheets: list[dict]) -> dict:
    zones = load_zones(path)
    zone = normalize_zone(spec, sheets, _next_id(zones))
    zones.append(zone)
    save_zones(path, zones)
    return zone


def delete_zone(path: Path, zone_id: str) -> None:
    zones = load_zones(path)
    kept = [zone for zone in zones if zone["id"] != zone_id]
    if len(kept) == len(zones):
        raise ZoneError(f"Zone {zone_id} is not on this job.", status_code=404)
    save_zones(path, kept)


def normalize_zone(spec: dict, sheets: list[dict], zone_id: str) -> dict:
    name = " ".join(str(spec.get("name") or "").split())
    if not name:
        raise ZoneError("A zone needs a name.")
    if len(name) > 80:
        raise ZoneError("A zone name must be 80 characters or fewer.")
    zone_type = str(spec.get("type") or "").strip()
    if zone_type not in ZONE_TYPE_VALUES:
        raise ZoneError("Zone type must be alternate, phase, floor, area, or by others / NIC.")
    side = str(spec.get("alternate") or "").strip()
    if zone_type == "alternate":
        if side not in ALTERNATE_SIDES:
            raise ZoneError("An alternate zone needs a side: add or deduct.")
    else:
        side = ""
    shape = str(spec.get("shape") or "").strip()
    if shape not in SHAPES:
        raise ZoneError("Shape must be rectangle or polygon.")
    try:
        page_index = int(str(spec.get("page_index")).strip())
    except (TypeError, ValueError) as exc:
        raise ZoneError("Pick the sheet this zone is on.") from exc
    sheet = next((item for item in sheets if int(item["page_index"]) == page_index), None)
    if sheet is None:
        raise ZoneError("That sheet is not in this set.")
    stored_shape, points = parse_points(spec.get("points"), shape)
    return {
        "alternate": side,
        "id": zone_id,
        "name": name,
        "page_index": page_index,
        "points": points,
        "shape": stored_shape,
        "sheet": sheet.get("sheet") or "",
        "type": zone_type,
    }


def parse_points(text, shape: str) -> tuple[str, list[list[float]]]:
    raw = str(text or "").strip()
    if not raw:
        raise ZoneError("Enter the zone corners.")
    if shape == "rectangle" and raw.count(",") == 3 and not re.search(r"[\s;]", raw):
        parts = [piece.strip() for piece in raw.split(",")]
        pairs = [(parts[0], parts[1]), (parts[2], parts[3])]
    else:
        pairs = []
        for chunk in re.split(r"[\s;]+", raw):
            if not chunk:
                continue
            bits = [piece.strip() for piece in chunk.split(",")]
            if len(bits) != 2:
                raise ZoneError("Each point needs an x and a y, such as 100,200 300,400.")
            pairs.append((bits[0], bits[1]))
    numbers = [(_number(x_text), _number(y_text)) for x_text, y_text in pairs]
    if shape == "rectangle":
        if len(numbers) != 2:
            raise ZoneError("A rectangle needs two corners.")
        return "rectangle", _rectangle(numbers[0], numbers[1])
    if len(numbers) < 3:
        raise ZoneError("A polygon needs at least three points.")
    return "polygon", [[x_value, y_value] for x_value, y_value in numbers]


def point_in_polygon(x: float, y: float, points: list[list[float]]) -> bool:
    """True when the point is inside or on the boundary. Even-odd fill."""

    count = len(points)
    if count < 3:
        return False
    for index in range(count):
        start = points[index]
        end = points[(index + 1) % count]
        if _on_segment(x, y, start[0], start[1], end[0], end[1]):
            return True
    inside = False
    previous = count - 1
    for index in range(count):
        xi, yi = points[index]
        xj, yj = points[previous]
        straddles = (yi > y) != (yj > y)
        if straddles and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        previous = index
    return inside


def assign_zones(rows: list[dict], zones: list[dict]) -> None:
    """Write zone membership onto each row. Same-type overlap assigns neither."""

    ordered = _sorted(zones)
    for row in rows:
        occurrences = row.get("occurrences") or []
        hits: list[tuple[int, dict]] = []
        for index, occurrence in enumerate(occurrences):
            x_value = (float(occurrence["x0"]) + float(occurrence["x1"])) / 2
            y_value = (float(occurrence["y0"]) + float(occurrence["y1"])) / 2
            page_index = int(occurrence["page_index"])
            for zone in ordered:
                if int(zone["page_index"]) != page_index:
                    continue
                if point_in_polygon(x_value, y_value, zone["points"]):
                    hits.append((index, zone))
        by_type: dict[str, list[tuple[int, dict]]] = {}
        for index, zone in hits:
            by_type.setdefault(zone["type"], []).append((index, zone))
        kept: list[dict] = []
        conflicts: list[dict] = []
        for zone_type in sorted(by_type):
            pairs = by_type[zone_type]
            per_point: dict[int, dict[str, dict]] = {}
            for index, zone in pairs:
                per_point.setdefault(index, {})[zone["id"]] = zone
            if any(len(found) > 1 for found in per_point.values()):
                names = sorted({zone["name"] for _index, zone in pairs})
                conflicts.append(
                    {
                        "names": names,
                        "text": _conflict_text(zone_type, names),
                        "type": zone_type,
                    }
                )
                continue
            kept.extend(zone for _index, zone in pairs)
        assignments = _unique_assignments(kept)
        hold = ""
        side = ""
        if row.get("effective") is not None:
            if any(zone["type"] == "by_others" for zone in assignments):
                hold = "by_others"
            elif any(zone["type"] == "alternate" for zone in assignments):
                hold = "alternate"
                sides = sorted(
                    {zone["alternate"] for zone in assignments if zone["type"] == "alternate" and zone["alternate"]}
                )
                side = sides[0] if len(sides) == 1 else ", ".join(sides)
        row["zones"] = assignments
        row["zone_conflicts"] = conflicts
        row["zone_conflict_text"] = " ".join(item["text"] for item in conflicts)
        row["zone_label"] = _zone_label(assignments)
        row["zone_hold"] = hold
        row["alternate_side"] = side
        row["counts_in_base"] = hold == ""
        row["zone_key"] = tuple(
            sorted(f"{zone['type']}:{zone['name']}:{zone['alternate']}" for zone in assignments)
        )


def zone_rollups(rows: list[dict]) -> list[dict]:
    """One quantity per zone name, type, system, and alternate side."""

    buckets: dict[tuple, dict] = {}
    for row in rows:
        effective = row.get("effective")
        if effective is None:
            continue
        system = row.get("system") or row.get("category") or ""
        for zone in row.get("zones") or []:
            side = zone.get("alternate") or ""
            key = (zone["name"], zone["type"], system, side)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "in_base": zone["type"] not in _HELD,
                    "item_ids": [],
                    "name": zone["name"],
                    "quantity": 0,
                    "sheets": [],
                    "side": side,
                    "system": system,
                    "type": zone["type"],
                    "type_label": type_label(zone["type"]),
                }
                buckets[key] = bucket
            bucket["quantity"] += int(effective)
            item_id = str(row["id"])
            if item_id not in bucket["item_ids"]:
                bucket["item_ids"].append(item_id)
            for sheet in zone.get("sheets") or []:
                if sheet and sheet not in bucket["sheets"]:
                    bucket["sheets"].append(sheet)
    return [buckets[key] for key in sorted(buckets)]


def _conflict_text(zone_type: str, names: list[str]) -> str:
    noun = _TYPE_NOUN.get(zone_type, "zone")
    shown = ", ".join(names)
    return f"In more than one {noun} ({shown}). Not assigned to either."


def _zone_label(assignments: list[dict]) -> str:
    parts = []
    for zone in assignments:
        piece = f"{type_label(zone['type'])}: {zone['name']}"
        if zone.get("alternate"):
            piece += f" ({zone['alternate']})"
        parts.append(piece)
    return "; ".join(parts)


def _unique_assignments(zones: list[dict]) -> list[dict]:
    found: dict[tuple, dict] = {}
    for zone in _sorted(zones):
        key = (zone["type"], zone["name"], zone.get("alternate") or "")
        current = found.get(key)
        if current is None:
            current = {
                "alternate": zone.get("alternate") or "",
                "ids": [],
                "name": zone["name"],
                "sheets": [],
                "type": zone["type"],
            }
            found[key] = current
        if zone["id"] not in current["ids"]:
            current["ids"].append(zone["id"])
        sheet = zone.get("sheet") or ""
        if sheet and sheet not in current["sheets"]:
            current["sheets"].append(sheet)
    return [found[key] for key in sorted(found)]


def _sorted(zones: list[dict]) -> list[dict]:
    return sorted(
        zones,
        key=lambda zone: (
            zone.get("name") or "",
            zone.get("type") or "",
            zone.get("sheet") or "",
            zone.get("id") or "",
        ),
    )


def _stored_zone(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    points = item.get("points")
    if not isinstance(points, list) or len(points) < 3:
        return None
    cleaned = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        try:
            cleaned.append([float(point[0]), float(point[1])])
        except (TypeError, ValueError):
            return None
    zone_type = item.get("type")
    if zone_type not in ZONE_TYPE_VALUES:
        return None
    shape = item.get("shape")
    if shape not in SHAPES:
        return None
    side = item.get("alternate") or ""
    if zone_type == "alternate" and side not in ALTERNATE_SIDES:
        return None
    if zone_type != "alternate":
        side = ""
    try:
        page_index = int(item.get("page_index"))
    except (TypeError, ValueError):
        return None
    name = " ".join(str(item.get("name") or "").split())
    zone_id = str(item.get("id") or "").strip()
    if not name or not zone_id:
        return None
    return {
        "alternate": side,
        "id": zone_id,
        "name": name,
        "page_index": page_index,
        "points": cleaned,
        "shape": shape,
        "sheet": str(item.get("sheet") or ""),
        "type": zone_type,
    }


def _next_id(zones: list[dict]) -> str:
    highest = 0
    for zone in zones:
        match = _ZONE_ID.fullmatch(str(zone.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"z{highest + 1}"


def _number(text: str) -> float:
    try:
        value = float(str(text).strip())
    except ValueError as exc:
        raise ZoneError("Zone corners must be numbers.") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise ZoneError("Zone corners must be numbers.")
    return value


def _rectangle(first: tuple[float, float], second: tuple[float, float]) -> list[list[float]]:
    left = min(first[0], second[0])
    right = max(first[0], second[0])
    top = min(first[1], second[1])
    bottom = max(first[1], second[1])
    if right - left <= _EDGE_EPSILON or bottom - top <= _EDGE_EPSILON:
        raise ZoneError("A rectangle needs two different corners.")
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _on_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> bool:
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > _EDGE_EPSILON:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -_EDGE_EPSILON:
        return False
    length_sq = (bx - ax) ** 2 + (by - ay) ** 2
    if dot - length_sq > _EDGE_EPSILON:
        return False
    return True
