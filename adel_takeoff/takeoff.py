"""Measure devices from a drawing set.

The count path reads the legend, matches geometry, stitches overlap, and
reads panel and circuit text. It does not import the routing package and
it does not call a conduit router.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adel_takeoff.classify import categorize
from adel_takeoff.code_notes import CodeClaim, Rfi, analyze_drawing_text
from adel_takeoff.extract import PageData, legend_entries, read_pdf
from adel_takeoff.links import link_for_cluster
from adel_takeoff.overlap import Instance, merged_link, stitch


@dataclass
class Occurrence:
    page_index: int
    sheet: str
    x0: float
    y0: float
    x1: float
    y1: float

    def as_dict(self) -> dict:
        return {
            "page_index": self.page_index,
            "sheet": self.sheet,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
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


@dataclass
class TakeoffResult:
    items: list[MeasuredItem]
    rfis: list[Rfi]
    code_claims: list[CodeClaim]


def _unmatched_item(cluster, sheet: str, reason: str) -> MeasuredItem:
    x0, y0, x1, y1 = cluster.bbox
    return MeasuredItem(
        label="Unmatched mark",
        category="unmatched",
        measurement="unmatched",
        measured_quantity=None,
        sheets=[sheet],
        panel="",
        circuit="",
        link_flag=reason,
        deduped=False,
        occurrences=[
            Occurrence(cluster.page_index, sheet, x0, y0, x1, y1)
        ],
    )


def run_takeoff(pdf_bytes: bytes) -> TakeoffResult:
    pages: list[PageData] = read_pdf(pdf_bytes)
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
    for page in pages:
        for cluster in page.clusters:
            if (page.index, cluster.bbox) in template_boxes:
                continue
            if cluster.fingerprint in ambiguous:
                unmatched.append(
                    _unmatched_item(
                        cluster,
                        page.sheet,
                        "legend geometry is ambiguous; not counted",
                    )
                )
                continue
            entry = templates.get(cluster.fingerprint)
            if entry is None:
                unmatched.append(
                    _unmatched_item(
                        cluster,
                        page.sheet,
                        "unmatched geometry; not counted",
                    )
                )
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
        occurrences = []
        for occurrence in device.occurrences:
            x0, y0, x1, y1 = occurrence.cluster.bbox
            occurrences.append(
                Occurrence(
                    occurrence.cluster.page_index,
                    occurrence.sheet,
                    x0,
                    y0,
                    x1,
                    y1,
                )
            )
        items.append(
            MeasuredItem(
                label=entry.label,
                category=categorize(entry.label),
                measurement="matched",
                measured_quantity=1,
                sheets=device.sheets,
                panel=link.panel,
                circuit=link.circuit,
                link_flag=link.flag,
                deduped=device.deduped,
                occurrences=occurrences,
            )
        )
    items.extend(unmatched)

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
    return TakeoffResult(items=items, rfis=rfis, code_claims=claims)
