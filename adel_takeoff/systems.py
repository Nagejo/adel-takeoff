"""Per-system quantities and the EC / by-others split.

Fire alarm quantities are grouped by device type and floor. A floor is
recorded only when the sheet title prints one. Rough-in only contributes
the box, ring, and sleeve or conduit allowance to EC, and the device to
by others.
"""

from __future__ import annotations

from adel_takeoff.classify import (
    BY_OTHERS_PARTY,
    device_type_label,
    party_for,
)
from adel_takeoff.schedule_link import floor_from_title


def floor_for_row(row: dict) -> str:
    for title in row.get("sheet_titles") or []:
        floor = floor_from_title(title)
        if floor:
            return floor
    return ""


def _measured(row: dict) -> int:
    value = row.get("measured")
    if value is None:
        return 0
    return int(value)


def _kept(row: dict) -> bool:
    if row.get("status") in ("rejected", "unmatched", "excluded"):
        return False
    if row.get("review_status") == "rejected" or row.get("exclusion"):
        return False
    return _measured(row) > 0


def system_rollup(rows: list[dict]) -> list[dict]:
    """One row per system, device type, and floor."""

    buckets: dict[tuple, dict] = {}
    order: list[tuple] = []
    for row in rows:
        if not _kept(row):
            continue
        system = row.get("system") or ""
        if not system or system == "other":
            continue
        device_type = row.get("device_type") or "other"
        floor = floor_for_row(row)
        key = (system, device_type, floor)
        if key not in buckets:
            order.append(key)
            buckets[key] = {
                "system": system,
                "category": row.get("category") or "",
                "device_type": device_type,
                "device_label": device_type_label(device_type),
                "floor": floor,
                "quantity": 0,
                "sheets": [],
                "item_ids": [],
                "scope": row.get("scope") or "",
            }
        bucket = buckets[key]
        bucket["quantity"] += _measured(row)
        for sheet in row.get("sheet_numbers") or []:
            if sheet and sheet not in bucket["sheets"]:
                bucket["sheets"].append(sheet)
        if row["id"] not in bucket["item_ids"]:
            bucket["item_ids"].append(row["id"])
    return [buckets[key] for key in order]


def fire_alarm_by_floor(rows: list[dict]) -> list[dict]:
    """Fire alarm counts only, by device type and floor."""

    return [row for row in system_rollup(rows) if row["category"] == "fire_alarm"]


def scope_split(rows: list[dict]) -> list[dict]:
    """EC work beside the work that stays with another vendor."""

    split = []
    for row in rows:
        if not _kept(row):
            continue
        mode = row.get("scope_mode") or ""
        if mode not in ("ec", "by_others", "rough_in"):
            continue
        quantity = _measured(row)
        base = {
            "system": row.get("system") or "",
            "category": row.get("category") or "",
            "device_type": row.get("device_type") or "",
            "device_label": device_type_label(row.get("device_type") or ""),
            "floor": floor_for_row(row),
            "sheet": ", ".join(row.get("sheet_numbers") or []),
            "item_ids": [row["id"]],
            "quantity": quantity,
        }
        if mode == "ec":
            split.append(
                {
                    **base,
                    "scope": "EC",
                    "party": "EC",
                    "note": "Device and rough-in by EC",
                }
            )
        elif mode == "by_others":
            split.append(
                {
                    **base,
                    "scope": "by others",
                    "party": party_for(row.get("category") or "", "by_others"),
                    "note": "By others",
                }
            )
        else:
            split.append(
                {
                    **base,
                    "scope": "EC",
                    "party": "EC",
                    "note": "Rough-in only: box, ring, and sleeve, conduit, or pull string allowance",
                }
            )
            split.append(
                {
                    **base,
                    "scope": "by others",
                    "party": BY_OTHERS_PARTY.get(row.get("category") or "", "by others"),
                    "note": "Device by others",
                }
            )
    return split
