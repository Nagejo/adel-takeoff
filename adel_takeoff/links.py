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


@dataclass(frozen=True)
class DeviceLink:
    panel: str
    circuit: str
    flag: str


def _distance_to_bbox(word: Word, bbox: tuple[float, float, float, float]) -> float:
    dx = max(bbox[0] - word.cx, 0.0, word.cx - bbox[2])
    dy = max(bbox[1] - word.cy, 0.0, word.cy - bbox[3])
    return (dx * dx + dy * dy) ** 0.5


def _line_text(cluster: Cluster, words: list[Word]) -> list[str]:
    """Tags anchored within the link radius, including the rest of that tag line."""

    anchors = [
        word
        for word in words
        if _distance_to_bbox(word, cluster.bbox) <= LINK_RADIUS_PT
    ]
    if not anchors:
        return []
    texts = []
    for line in group_lines(words):
        if not any(anchor in line for anchor in anchors):
            continue
        kept = [
            word
            for word in line
            if _distance_to_bbox(word, cluster.bbox) <= 160
        ]
        if kept:
            texts.append(" ".join(word.text for word in kept))
    return texts


def link_for_cluster(cluster: Cluster, words: list[Word]) -> DeviceLink:
    pairs: set[tuple[str, str]] = set()
    for text in _line_text(cluster, words):
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                pairs.add((match.group(1).upper(), match.group(2)))
    if not pairs:
        return DeviceLink("", "", MISSING)
    if len(pairs) > 1:
        return DeviceLink("", "", CONFLICT)
    panel, circuit = next(iter(pairs))
    return DeviceLink(panel, circuit, "")
