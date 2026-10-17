# Adel takeoff roadmap

Build order for the Adel Electric quantity takeoff engine: plans in, a counted Excel workbook and a marked-up PDF out. Alan approved this order on 17 Oct 2026 from the public-page research in [research/takeoff-competitor-research.md](research/takeoff-competitor-research.md).

This document describes capabilities. It does not carry any vendor's code, assets, or branding.

## Where the statuses come from

`main` contains this plan and the license. The engine itself is the unmerged draft on `cursor/estimating-takeoff-v1-8b7a` (pull request #1, commit `27a059d`). Every status below was read from that branch: the README, `adel_takeoff/`, and `tests/`.

| Status | Meaning on that branch |
| --- | --- |
| done | The capability in the description is implemented. |
| partial | A slice of it is implemented. The rest of the line is still open. |
| planned | The branch reserves the work and does not implement it. |
| new | The branch does not implement it and does not reserve it. |

Difficulty is the rough remaining effort from that same ranking: Low, Low–Med, Med, Med–High, or High.

The v1 engine already uploads a PDF, reads a symbol legend, counts power devices and lighting fixtures, stitches a match-line overlap so a duplicated device is counted once, reads panel and circuit text beside a device, stores a count correction and an allowance, prices only numbers the estimator enters, and writes Excel plus a marked-up PDF. Those pieces are the basis for the partial rows. They are not a separate build item.

## 1–7. Build these first

| # | Capability | Description | Status | Difficulty |
| --- | --- | --- | --- | --- |
| 1 | Every count traceable | Each count has a numbered mark on the sheet, a sheet reference on its Excel line, the counting math, and a confidence flag. | partial | Low |
| 2 | Review step | Accept, reject, or correct an item, add a missed one, and exclude existing-to-remain and not-in-contract work before export. | partial | Med |
| 3 | Fixture and panel schedules | Read fixture and panel schedules, and link type, circuit, and mounting height to each device. | partial | Med |
| 4 | Assemblies | Each device expands into an editable box, plate, raceway, wire, and labor hours. | new | Med |
| 5 | Drawing scale | Detect drawing scale automatically, and refuse to give lengths when it cannot. | new | Low–Med |
| 6 | Revisions and addenda | Compare revisions and addenda and show the count differences. | new | Med–High |
| 7 | Low-voltage, fire alarm, and controls | Count low-voltage, fire alarm, and controls/keypad devices as their own categories. | partial | Med |

Research-table numbers for this block are 1, 2, 3, 4, 5, 7, and 8. Text search (research 6) follows in the next block.

### Why each status

1. **partial.** `export_pdf.py` draws a box around each occurrence and a short label (measured, counted once, corrected, uncounted, or by others). The mark has no number. `export_excel.py` writes a Sheets column of joined sheet names and a Review flag taken from `link_flag`. `db.py` stores each occurrence's page, sheet, and bounding box in `occurrences_json`. Counting math and a confidence flag are still open.
2. **partial.** `templates/review.html` and `POST /jobs/{id}/items/{id}/correction` store an override quantity beside the measured count, plus material unit cost and labor hours. `POST /jobs/{id}/allowances` adds a quantity that requires a reason. Job settings can leave landscape lighting and lighting control by others (`classify.in_scope`, `include_landscape`, `include_lighting_control`). Per-item accept and reject, adding a missed symbol on the sheet, and excluding existing-to-remain or not-in-contract work are still open. `export_excel.py` writes every review row.
3. **partial.** `extract.legend_entries` reads the symbol legend (geometry plus the label beside it). `links.py` reads panel and circuit from text within 48 pt (`PANEL … CKT`, `HOMERUN`, or a tag like `P1-12`) and leaves the link blank, with a flag, when that text is missing or conflicts. Fixture-schedule parsing, panel-schedule parsing, and mounting height are still open.
4. **new.** `costing.line_direct_cost` multiplies a quantity by an entered material unit cost and entered labor hours. Both stay blank until the estimator fills them in. An assembly that expands one device into box, plate, raceway, wire, and hours is still open.
5. **new.** No module reads a drawing scale. The engine does not emit lengths. `routing/protocol.py` defines `ConduitRouter`; `UnimplementedConduitRouter.route` raises `NotImplementedError` and produces no route. The count path does not call it.
6. **new.** The v1 tree has no revision upload, addenda detection, overlay, or count-delta report.
7. **partial.** `classify.py` labels power, lighting, landscape lighting, lighting control, and other from the legend text. Landscape lighting and lighting control stay by others unless the job includes them. Low-voltage, fire alarm, and keypad categories are still open. `code_notes.py` can file an RFI when power and Class 2 or communications share a raceway; that note is not a device category.

## 8–18. Then these, in the research order

This block is research items 6 and 9 through 18, in that order.

| # | Research # | Capability | Description | Status | Difficulty |
| --- | --- | --- | --- | --- | --- |
| 8 | 6 | Search text and tags | Search fixture tags and panel names across the whole set. | new | Low |
| 9 | 9 | Symbol library and search by example | Keep a company symbol library, and find symbols missing from the legend by example, including rotation and mirroring, with a similarity cutoff the estimator sets. | new | Med |
| 10 | 10 | Zones and areas | Limit a count to a zone or area for an alternate, a phase, or a floor. | new | Low–Med |
| 11 | 11 | Discrepancy notes | Flag a device count that disagrees with its schedule, and a panel schedule that disagrees with the one-line. | new | Med |
| 12 | 12 | Keynotes and general notes | Read keynotes and general notes and link them to the items they govern. | new | Med |
| 13 | 13 | Accuracy benchmark | Keep ground-truth plan sets and run a deterministic scorecard with the regression tests. | new | Low–Med |
| 14 | 14 | Sheet classification and cleanup | Classify sheets (plans, schedules, legends, risers) and strip background, clouds, and grids before detection. | new | Med |
| 15 | 15 | Estimating-system export | Export into estimating-system formats such as Accubid, McCormick, and ConEst, where the format is public. | new | Med |
| 16 | 16 | Estimator's takeoff beside the engine | Show the estimator's own takeoff next to the engine's quantities. | new | Low |
| 17 | 17 | Stitch sheets without double counting | Treat a device that continues across sheets as one count. Match-line overlap is in v1; stitching on a column grid is still open. | partial | High |
| 18 | 18 | Branch routing and home runs | Group circuits, place home-run boxes, route branches, and size wire with voltage drop and derating. | planned | High |

### Why each status

8. **new.** `extract.page_words` and `code_notes.analyze_drawing_text` read page text for the legend, nearby tags, and code sentences. A search across the set is still open.
9. **new.** `extract._fingerprint` counts a mark only when its geometry equals a legend symbol. Size, orientation, and stroke order are part of that identity. A saved library, example search, rotation and mirror tolerance, and a similarity cutoff are still open.
10. **new.** The v1 tree has no zone, alternate, phase, or floor selection.
11. **new.** RFIs today cover a missing legend, a match line that cannot be stitched, drawing text that says NEC 2023, and power mixed with Class 2 or communications (`takeoff.py`, `code_notes.py`). Plan-versus-schedule and panel-schedule-versus-one-line checks are still open.
12. **new.** Requirement sentences on the drawing can become job-level code claims (`code_notes.py`). Keynotes and general notes linked to devices are still open.
13. **new.** `tests/test_app.py`, `tests/test_rules.py`, `tests/test_routing.py`, and `tests/drawings.py` check rules on PDFs the tests draw themselves. Ground-truth plan sets and a scorecard are still open.
14. **new.** `extract.sheet_name` reads a SHEET label, and the legend is found by its heading. Sheet-type classification and background, cloud, and grid cleanup are still open.
15. **new.** Downloads are `export.xlsx` (`export_excel.py`) and `export.pdf` (`export_pdf.py`). Writers for other estimating systems are still open.
16. **new.** The review stores one correction on the engine's own line (`override_quantity` next to `measured_quantity`). A second, independent takeoff shown beside those numbers is still open.
17. **partial.** `overlap.stitch` places sheets that share a match-line key and one overlap depth into one frame, counts a device in that overlap once, and keeps every sheet that contributed (`deduped`, Excel "counted once"). The README section "What v1 does" describes that behavior. Stitching by column grid is still open, which is why the remaining difficulty stays High.
18. **planned.** The README section "What v1 does not do" lists conduit routing, wire sizing, voltage drop, and derating. `routing/protocol.py` is the reserved seam, and the only router raises `NotImplementedError`. Build this only after the patent review below.

## Caution: automatic home runs and branch routing

McCormick Systems holds US Patent 11,120,171 on automatic home runs. A patent review is required before any home-run or branch-routing automation is built. That review covers drawing or measuring wire and conduit runs, grouping circuits into home runs, and placing home-run boxes as an automatic route. Item 18 stays blocked until the review is done.

The v1 engine does not route. `UnimplementedConduitRouter` produces no route, and the count path does not import `adel_takeoff.routing`.

## Later / skip for now

Leave these out of the current build.

| Capability | Description | Status | Difficulty |
| --- | --- | --- | --- |
| Chat with plans | Ask questions in a conversation grounded in the drawing set. | new | not sized |
| Revit / BIM output | Write the takeoff into a Revit or other BIM model. | new | not sized |
| Done-for-you human QA | Send the set to a person who checks the takeoff and returns Excel. | new | not sized |

Chat and a human QA service do not appear in the v1 app. The README lists Revit / BIM output with the other work v1 does not do. Difficulty was not ranked for these three.

## Sources

Public pages only, gathered 17 Oct 2026. The notes, including the ranked table this order comes from, are in [research/takeoff-competitor-research.md](research/takeoff-competitor-research.md).

- Drawer AI: <https://drawer.ai/> · <https://drawer.ai/pricing> · <https://drawer.ai/about-us>
- Aginera: <https://aginera.ai/docs/takeoff/electrical> · <https://aginera.ai/solutions/electrical-estimating> · <https://aginera.ai/mep-ai-benchmark>
- Groundbook AI: <https://groundbook.ai/quantity-takeoff/> · <https://groundbook.ai/for/contractors/> · <https://groundbook.ai/pricing/>
- Togal.AI: <https://www.togal.ai/trades/electrical> · <https://www.togal.ai/features> · <https://www.youtube.com/watch?v=vsnRqiupk1A>
- Beam AI (Attentive.ai): <https://www.ibeam.ai/subcontractors/electrical> · <https://www.ibeam.ai/pricing>
- Trimble LiveCount: <https://www.trimble.com/en/blog/construction/article/powerful-new-automated-takeoff-capabilities-in-trimble-livecount-digital-takeoff-software>
- Kreo: <https://help-takeoff.kreo.net/en/articles/5481213-auto-count> · <https://www.kreo.net/trades/electrical-estimating-software>
- ConEst SureCount / IntelliBid: <https://conest.com/electrical-takeoff-software/>
- McCormick Systems: <https://www.mccormicksys.com/blog/how-mccormicks-electrical-estimating-features-work/> · <https://www.mccormicksys.com/ai-statement/>
- Bluebeam Max was checked and left out of the build list. Its public FAQ says it does not reliably identify electrical symbols yet: <https://community.bluebeam.com/kb/bluebeam-max-faqs>
