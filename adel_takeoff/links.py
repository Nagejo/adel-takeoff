"""Panel and circuit links are read from text near a device.

If no tag matches, the link stays blank and is flagged. Nothing is inferred
from the device type or from another device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from adel_takeoff.constants import LINK_RADIUS_PT
from adel_takeoff.extract import Cluster, Word, group_lines

PATTERNS = (
    re.compile(
        r"\bPANEL\s+([A-Z]{1,6}\d{0,4})\s+(?:CKT|CIR|CIRCUIT)\s*#?\s*(\d{1,3})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bHOMERUN\s+([A-Z]{1,6}\d{0,4})\s*[-/]\s*(\d{1,3})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b([A-Z]{1,4}\d{1,2})\s*[-/]\s*(\d{1,3})\b", re.IGNORECASE),
)

MISSING = "panel and circuit not readable"
CONFLICT = "panel and circuit text conflicts; left blank"

# An explicit grid callout. A line of this form is not a panel tag.
_GRID_LINE = re.compile(
    r"^GRID\s+([A-Z]{1,2})\s*[-]?\s*(\d{1,2})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeviceLink:
    panel: str
    circuit: str
    flag: str


def _distance_to_bbox(word: Word, bbox: tuple[float, float, float, float]) -> float:
    dx = max(bbox[0] - word.cx, 0.0, word.cx - bbox[2])
    dy = max(bbox[1] - word.cy, 0.0, word.cy - bbox[3])
    return (dx * dx + dy * dy) ** 0.5


def texts_near_bbox(
    bbox: tuple[float, float, float, float],
    words: list[Word],
    radius: float = LINK_RADIUS_PT,
) -> list[str]:
    """Text lines anchored within the link radius of a device box."""

    anchors = [word for word in words if _distance_to_bbox(word, bbox) <= radius]
    if not anchors:
        return []
    texts = []
    for line in group_lines(words):
        if not any(anchor in line for anchor in anchors):
            continue
        kept = [word for word in line if _distance_to_bbox(word, bbox) <= 160]
        if kept:
            texts.append(" ".join(word.text for word in kept))
    return texts


def _line_text(cluster: Cluster, words: list[Word]) -> list[str]:
    """Tags anchored within the link radius, including the rest of that tag line."""

    return texts_near_bbox(cluster.bbox, words)


def is_grid_line(text: str) -> bool:
    return _GRID_LINE.match(text.strip()) is not None


def _grid_refs(bbox: tuple[float, float, float, float], words: list[Word]) -> list[str]:
    found: list[str] = []
    for line in group_lines(words):
        text = " ".join(word.text for word in line).strip()
        match = _GRID_LINE.match(text)
        if match is None:
            continue
        if not any(_distance_to_bbox(word, bbox) <= LINK_RADIUS_PT for word in line):
            continue
        ref = f"{match.group(1).upper()}-{match.group(2)}"
        if ref not in found:
            found.append(ref)
    return found


def grid_for_bbox(bbox: tuple[float, float, float, float], words: list[Word]) -> str:
    """Return one nearby GRID callout.

    Zero callouts, or more than one, stay blank. Nothing is inferred from
    the sheet border.
    """

    found = _grid_refs(bbox, words)
    if len(found) == 1:
        return found[0]
    return ""


def grid_for_cluster(cluster: Cluster, words: list[Word]) -> str:
    return grid_for_bbox(cluster.bbox, words)


def link_for_cluster(cluster: Cluster, words: list[Word]) -> DeviceLink:
    pairs: set[tuple[str, str]] = set()
    for text in _line_text(cluster, words):
        if _GRID_LINE.match(text.strip()):
            continue
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                pairs.add((match.group(1).upper(), match.group(2)))
    if not pairs:
        return DeviceLink("", "", MISSING)
    if len(pairs) > 1:
        return DeviceLink("", "", CONFLICT)
    panel, circuit = next(iter(pairs))
    return DeviceLink(panel, circuit, "")
