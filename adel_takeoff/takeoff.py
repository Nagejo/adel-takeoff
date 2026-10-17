"""Measure devices from a drawing set.

The count path reads the legend, matches geometry, stitches overlap, and
reads panel and circuit text. Each kept mark gets a stable id. An exact
legend match records the detector's equality score. A mark that does not
match records no score. This module does not import the routing package
and it does not call a conduit router.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adel_takeoff.classify import classify_label
from adel_takeoff.code_notes import CodeClaim, Rfi, analyze_drawing_text
from adel_takeoff.constants import EXACT_MATCH_SCORE
from adel_takeoff.extract import PageData, legend_entries, read_pdf
from adel_takeoff.links import grid_for_cluster, link_for_cluster
from adel_takeoff.overlap import Instance, merged_link, stitch


@dataclass
class Occurrence:
    page_index: int
    sheet: str
    sheet_title: str
    x0: float
    y0: float
    x1: float
    y1: float
    grid: str = ""

    def as_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "sheet": self.sheet,
            "sheet_title": self.sheet_title,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "grid": self.grid,
        }


@dataclass
class MeasuredItem:
    label: str
    category: str
    measurement: str
    measured_quantity: int | None
    sheets: list[str]
    panel: str
    circuit: str
    link_flag: str
    deduped: bool
    occurrences: list[Occurrence] = field(default_factory=list)
    method: str = "legend_match"
    match_score: float | None = None
    item_id: str = ""


@dataclass
class TakeoffResult:
    items: list[MeasuredItem]
    rfis: list[Rfi]
    code_claims: list[CodeClaim]


def _occurrence(cluster, page: PageData) -> Occurrence:
    x0, y0, x1, y1 = cluster.bbox
    return Occurrence(
        page_index=cluster.page_index,
        sheet=page.sheet,
        sheet_title=page.sheet_title,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        grid=grid_for_cluster(cluster, page.words),
    )


def _unmatched_item(cluster, page: PageData, reason: str) -> MeasuredItem:
    return MeasuredItem(
        label="Unmatched mark",
        category="unmatched",
        measurement="unmatched",
        measured_quantity=None,
        sheets=[page.sheet],
        panel="",
        circuit="",
        link_flag=reason,
        deduped=False,
        occurrences=[_occurrence(cluster, page)],
        method="unmatched",
        match_score=None,
    )


def assign_item_ids(items: list[MeasuredItem]) -> None:
    """Number marks in sheet order. The same geometry keeps the same id."""

    def sort_key(pair: tuple[int, MeasuredItem]) -> tuple:
        index, item = pair
        if item.occurrences:
            occurrence = item.occurrences[0]
            return (
                occurrence.page_index,
                round(occurrence.y0, 2),
                round(occurrence.x0, 2),
                item.label,
                index,
            )
        return (9999, 0, 0, item.label, index)

    ordered = sorted(enumerate(items), key=sort_key)
    for number, (_index, item) in enumerate(ordered, start=1):
        item.item_id = str(number)


def run_takeoff(pdf_bytes: bytes) -> TakeoffResult:
    pages: list[PageData] = read_pdf(pdf_bytes)
    pages_by_index = {page.index: page for page in pages}
    entries = []
    template_boxes: set[tuple] = set()
    for page in pages:
        for entry in legend_entries(page):
            entries.append(entry)
            template_boxes.add((entry.page_index, entry.bbox))

    templates = {}
    ambiguous: set[tuple] = set()
    for entry in entries:
        if entry.ambiguous or entry.fingerprint in templates:
            ambiguous.add(entry.fingerprint)
            templates.pop(entry.fingerprint, None)
            continue
        templates[entry.fingerprint] = entry
    for fingerprint in ambiguous:
        templates.pop(fingerprint, None)

    instances: list[Instance] = []
    unmatched: list[MeasuredItem] = []
    unlegended: list[tuple] = []
    for page in pages:
        for cluster in page.clusters:
            if (page.index, cluster.bbox) in template_boxes:
                continue
            if cluster.fingerprint in ambiguous:
                unmatched.append(
                    _unmatched_item(
                        cluster,
                        page,
                        "legend geometry is ambiguous; not counted",
                    )
                )
                continue
            entry = templates.get(cluster.fingerprint)
            if entry is None:
                unmatched.append(
                    _unmatched_item(
                        cluster,
                        page,
                        "unmatched geometry; not counted",
                    )
                )
                unlegended.append((cluster, page))
                continue
            instances.append(
                Instance(
                    cluster=cluster,
                    sheet=page.sheet,
                    fingerprint=cluster.fingerprint,
                    link=link_for_cluster(cluster, page.words),
                )
            )

    devices, stitch_notes = stitch(pages, instances)
    items: list[MeasuredItem] = []
    for device in devices:
        entry = templates.get(device.fingerprint)
        if entry is None:
            continue
        link = merged_link(device)
        occurrences = [
            _occurrence(occurrence.cluster, pages_by_index[occurrence.cluster.page_index])
            for occurrence in device.occurrences
        ]
        classified = classify_label(entry.label, entry.system)
        items.append(
            MeasuredItem(
                label=entry.label,
                category=classified.category,
                measurement="matched",
                measured_quantity=1,
                sheets=device.sheets,
                panel=link.panel,
                circuit=link.circuit,
                link_flag=link.flag,
                deduped=device.deduped,
                occurrences=occurrences,
                method="legend_match",
                match_score=EXACT_MATCH_SCORE,
            )
        )
    items.extend(unmatched)
    assign_item_ids(items)

    drawing_text = "\n".join(page.text for page in pages)
    rfis, claims = analyze_drawing_text(drawing_text)
    if not entries:
        rfis.insert(
            0,
            Rfi(
                topic="Symbol legend not found",
                detail=(
                    "No symbol legend was found. No device quantities were counted."
                ),
                sections="",
                disposition="rfi",
            ),
        )
    for note in stitch_notes.rfis:
        rfis.append(Rfi(topic="Overlap", detail=note, sections="", disposition="rfi"))
    for cluster, page in unlegended:
        x0, y0, x1, y1 = cluster.bbox
        rfis.append(
            Rfi(
                topic="Unlegended symbol",
                detail=(
                    f"A mark on {page.sheet} at ({(x0 + x1) / 2:.1f}, {(y0 + y1) / 2:.1f}) pt "
                    "is on the plan and is missing from the legend. It can be sent as an RFI."
                ),
                sections="",
                disposition="rfi",
            )
        )
    return TakeoffResult(items=items, rfis=rfis, code_claims=claims)
