"""Stitch devices that fall inside a shared match-line overlap.

The shared coordinate is (distance along the match line, distance inward
from the match line). Two instances with the same legend geometry and the
same shared coordinate are one device. Sheets that contributed are kept.

If the match-line key or the overlap depth is missing, or the depths
disagree, nothing is merged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adel_takeoff.extract import Cluster, MatchLine, PageData
from adel_takeoff.links import DeviceLink


@dataclass
class Instance:
    cluster: Cluster
    sheet: str
    fingerprint: tuple
    link: DeviceLink
    in_overlap: bool = False
    along: float | None = None
    into: float | None = None


@dataclass
class StitchedDevice:
    fingerprint: tuple
    occurrences: list[Instance] = field(default_factory=list)
    deduped: bool = False

    @property
    def sheets(self) -> list[str]:
        names: list[str] = []
        for occurrence in self.occurrences:
            if occurrence.sheet not in names:
                names.append(occurrence.sheet)
        return names


@dataclass
class StitchNotes:
    rfis: list[str] = field(default_factory=list)


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def _orient(line: MatchLine) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return the line start and a unit direction, top-to-bottom or left-to-right."""

    horizontal = abs(line.y1 - line.y0) <= abs(line.x1 - line.x0)
    if horizontal:
        if line.x0 <= line.x1:
            start = (line.x0, line.y0)
            end = (line.x1, line.y1)
        else:
            start = (line.x1, line.y1)
            end = (line.x0, line.y0)
    else:
        if line.y0 <= line.y1:
            start = (line.x0, line.y0)
            end = (line.x1, line.y1)
        else:
            start = (line.x1, line.y1)
            end = (line.x0, line.y0)
    return start, _unit(end[0] - start[0], end[1] - start[1])


def _inward_normal(
    start: tuple[float, float],
    direction: tuple[float, float],
    centers: list[tuple[float, float]],
) -> tuple[float, float] | None:
    if not centers:
        return None
    centroid = (
        sum(point[0] for point in centers) / len(centers),
        sum(point[1] for point in centers) / len(centers),
    )
    normal_a = (-direction[1], direction[0])
    normal_b = (direction[1], -direction[0])
    toward = (centroid[0] - start[0], centroid[1] - start[1])
    dot_a = toward[0] * normal_a[0] + toward[1] * normal_a[1]
    dot_b = toward[0] * normal_b[0] + toward[1] * normal_b[1]
    if abs(dot_a - dot_b) < 1e-6:
        return None
    return normal_a if dot_a > dot_b else normal_b


def _place(
    center: tuple[float, float],
    start: tuple[float, float],
    direction: tuple[float, float],
    inward: tuple[float, float],
) -> tuple[float, float]:
    relative = (center[0] - start[0], center[1] - start[1])
    along = relative[0] * direction[0] + relative[1] * direction[1]
    into = relative[0] * inward[0] + relative[1] * inward[1]
    return along, into


def _line_is_segment(line: MatchLine) -> bool:
    return (line.x0 != line.x1) or (line.y0 != line.y1)


def stitch(pages: list[PageData], instances: list[Instance]) -> tuple[list[StitchedDevice], StitchNotes]:
    notes = StitchNotes()
    by_page: dict[int, list[Instance]] = {}
    for instance in instances:
        by_page.setdefault(instance.cluster.page_index, []).append(instance)

    frames: dict[int, tuple[tuple[float, float], tuple[float, float], tuple[float, float], float]] = {}
    grouped: dict[str, list[MatchLine]] = {}
    for page in pages:
        for line in page.match_lines:
            if not line.key:
                notes.rfis.append(
                    "A match line has no key. Overlap on that sheet was not stitched."
                )
                continue
            grouped.setdefault(line.key, []).append(line)

    for key, lines in grouped.items():
        usable = [line for line in lines if _line_is_segment(line)]
        pages_for_key = {line.page_index for line in usable}
        if len(pages_for_key) < 2:
            continue
        depths = {line.depth for line in usable}
        if None in depths or len(depths) != 1:
            notes.rfis.append(
                f"Match line {key} has no single overlap depth. "
                "Devices were not merged."
            )
            continue
        depth = next(iter(depths))
        assert depth is not None
        for line in usable:
            centers = [item.cluster.center for item in by_page.get(line.page_index, [])]
            start, direction = _orient(line)
            inward = _inward_normal(start, direction, centers)
            if inward is None:
                notes.rfis.append(
                    f"Match line {key} on {pages[line.page_index].sheet} has no "
                    "inward side. Devices on that sheet were not merged."
                )
                continue
            frames[line.page_index] = (start, direction, inward, depth)

    for instance in instances:
        frame = frames.get(instance.cluster.page_index)
        if frame is None:
            continue
        start, direction, inward, depth = frame
        along, into = _place(instance.cluster.center, start, direction, inward)
        if -1.0 <= into <= depth + 0.5:
            instance.in_overlap = True
            instance.along = along
            instance.into = into

    buckets: dict[tuple, list[Instance]] = {}
    order: list[tuple] = []
    for instance in instances:
        if instance.in_overlap and instance.along is not None and instance.into is not None:
            key = (
                "overlap",
                instance.fingerprint,
                round(instance.along),
                round(instance.into),
            )
        else:
            key = (
                "local",
                instance.cluster.page_index,
                instance.fingerprint,
                round(instance.cluster.center[0], 1),
                round(instance.cluster.center[1], 1),
            )
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(instance)

    devices: list[StitchedDevice] = []
    for key in order:
        group = buckets[key]
        device = StitchedDevice(
            fingerprint=group[0].fingerprint,
            occurrences=group,
            deduped=len({item.sheet for item in group}) > 1,
        )
        devices.append(device)
    return devices, notes


def merged_link(device: StitchedDevice) -> DeviceLink:
    """Use one readable link. Conflicting sheet text stays blank."""

    from adel_takeoff.links import CONFLICT, MISSING

    found: set[tuple[str, str]] = set()
    for occurrence in device.occurrences:
        if occurrence.link.panel or occurrence.link.circuit:
            found.add((occurrence.link.panel, occurrence.link.circuit))
    if not found:
        return DeviceLink("", "", MISSING)
    if len(found) > 1:
        return DeviceLink("", "", CONFLICT)
    panel, circuit = next(iter(found))
    return DeviceLink(panel, circuit, "")
