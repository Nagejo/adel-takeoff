# Adel Electric quantity takeoff

Internal estimating tool for Adel Electric, LLC. Version 1 reads a multi-page electrical PDF, counts power devices and lighting fixtures from the symbol legend, and writes a quantity report.

This is an internal tool. It is not a product for other contractors. It is not Drawer.ai.

## What v1 does

- Upload a multi-page PDF.
- Read the symbol legend (geometry plus the label beside it).
- Match sheet marks to legend symbols by geometry. A mark that does not match is left uncounted and flagged.
- Count power devices and lighting fixtures. Landscape lighting and lighting control stay by others unless the job includes them.
- Detect match lines, place the overlap in one coordinate frame, and count a duplicated device once. The sheets that contributed are kept.
- Read panel and circuit identifiers from text next to the device. If that text is missing, the link stays blank and is flagged.
- Store a count correction beside the measured value, and store an allowance only when someone enters a quantity and a reason.
- Direct cost is entered material unit cost times quantity, plus entered labor hours times that line's labor rate. Material cost and labor hours stay blank until the estimator fills them in.
- Markup is entered per job as a decimal. Blank means markup is not applied. A value greater than 5 is rejected. There is no default markup.
- The code edition follows the permit-issue date: FBC 8th Edition and NEC 2020 through 2026-12-31, then FBC 9th Edition and NEC 2023.
- Download an Excel quantity report and a marked-up PDF.
- Keep the job in local SQLite so review and download survive a refresh.
- Give every detected mark a stable id and draw that number on the marked-up PDF. The same id is the Excel item id.
- On each quantity line, record the sheet number, the sheet title when the drawing prints one, the location (a grid callout when one is printed, otherwise page coordinates), the item ids rolled into the line, how the count was made, and a confidence flag.
- Confidence is high, check, or low only from the detector's score and the cutoffs in `adel_takeoff/constants.py`. An exact legend match records a score of 1.0. A miss, and a mark you add by hand, record no score, so the flag stays blank.
- Review each mark before you treat the export as final: accept it, reject it, correct its type or panel/circuit tag, or exclude it as existing-to-remain, NIC, or by others. Excluded marks stay on an Excluded sheet and are drawn differently. They are not in the counted total.
- Add a missed mark by sheet and location. That adds one item. It does not invent a quantity.
- Store those decisions in `reviews/<job-id>.json` next to the job. A later export reads that file and reproduces the same result.
- Read a lighting fixture schedule and a panel schedule when the drawing prints one. A fixture type tag is linked to that schedule row's description, mounting, and voltage. A panel and circuit tag is linked to that circuit's schedule row. Mounting height is copied from the schedule, or from a height tag on the plan when the schedule does not print one.
- When a type tag has no schedule row, a schedule row has no plan marks, a plan circuit is missing from the panel schedule, or the plan count and the schedule count differ, both facts are written to a Discrepancies sheet. The plan count is kept. A discrepancy lowers a recorded confidence flag to check and does not create a flag when the detector recorded no score.
- The Excel report adds the copied schedule columns and a per-type rollup by sheet and floor. Summing the per-sheet counts can count an overlap device twice. The rollup also keeps the unique device count.
- Expand each counted device into the parts on its assembly: device, plate, box, a raceway allowance, a conductor allowance, miscellaneous parts, and labor hours per unit. Part quantity is the takeoff count times the per-unit quantity. The Materials sheet rolls those parts up and keeps the source item ids. The Labor sheet rolls hours up per line and in total. Rates stay on their own lines.
- Assemblies are edited in `assemblies/starter.json`. Per-job overrides are `data/assemblies/<job-id>.json`, and the assemblies page writes that file. Every starter quantity and labor hour is an example. The export shows an unconfirmed-assembly warning, and example hours stay out of direct cost, until the estimator confirms that assembly. Confirming an assembly does not make the hours a published labor standard. Raceway and conductor lengths are allowances. Excluded and rejected devices are not expanded.
- Read each sheet's scale from printed scale text, such as `1/8" = 1'-0"` or `1:100`, and from a graphic scale bar when one is drawn. The reading records its source and the page region it came from. When a sheet has more than one scale, each scale is kept with the viewport that contains it. Scales that cannot be tied to separate regions are ambiguous.
- Refuse a length when the scale is missing, marked NTS, ambiguous, or the printed text and the bar disagree by more than 5 percent. The refusal says why. Raceway and conductor stay on the assembly allowance. A scale is never guessed.
- An estimator can set a scale by picking one or by entering a known dimension between two points. That manual scale is stored in `data/scales/<job-id>.json` with who set it and when, and it overrides detection.
- A polyline measure tool reports length in feet only on a sheet or viewport with a valid scale. Measured lengths are on the Excel Measured sheet with the sheet, the scale source, and method `measured`. They are separate from allowance lines.
- Count low voltage and data, fire alarm, and controls in addition to power and lighting. Low voltage covers data and voice outlets, wireless access points, TV, coax, AV and speaker locations, cameras, intercom and door stations, and access control. Fire alarm covers smoke, heat, CO and combination detectors, horn/strobes, pull stations, duct detectors, modules, the FACP, and annunciators. Controls cover lighting keypads, dimmers that belong to a control system, shade controls, thermostats, and occupancy or vacancy sensors. Each system has its own legend heading and label rules. Item ids, sheet, location, count method, confidence, and review actions stay the same.
- Set a scope for each system: by EC, by others, or rough-in only. By others names the AV/low-voltage integrator, the fire alarm vendor, or the lighting control vendor. Rough-in only expands to the box, ring, and a sleeve, conduit, or pull string allowance. The device stays by others. Landscape lighting and lighting control default to by others unless the job sets otherwise. Low voltage, fire alarm, and controls do too.
- Fire alarm quantities are by device type and floor. The floor is taken from the sheet title when the drawing prints one. Excel adds a By system rollup and a Scope Split sheet for EC versus by others.
- Compare a revised drawing set with the previous one from the job's Revision page. Sheets match on sheet number. A unique printed title pairs a sheet whose number changed. Added, removed, and renumbered sheets are reported. Each matched sheet is aligned and diffed. The overlay PDF marks additions in blue, deletions in red, and clouds the change regions.
- Takeoff counts are compared by tag, sheet, and area, with old quantity, new quantity, and delta on the Excel Revision Changes sheet. Fixture and panel schedule row changes are on that sheet too. An item that clearly persists keeps its old item id. Review decisions carry forward only for unchanged items. Changed items go back to check, including when the detector recorded no score. That check is a revision flag.
- If alignment fails or agreement is below the cutoff in `adel_takeoff/constants.py`, that sheet is refused. The refusal says why. No colored overlay and no position-based count diff are produced for it.
- Read a scanned sheet. A page with no usable text layer is rendered and read with Tesseract. Each word is kept with its box and confidence in `data/text/<job-id>.json`. A page that already has PDF text keeps that text. Title blocks, scale text, legend labels, and schedules use those words. Anything read by OCR is marked source `ocr`.
- Low-confidence OCR does not set a scale or a schedule value on its own. That reading is withheld and sent to check. The cutoff is the same check cutoff in `adel_takeoff/constants.py`.
- Search a tag, a panel name, or any other text across the set. Hits list the sheet and the location, and a hit opens on the sheet with the spot marked. The same hits are on the Excel Text search sheet.
- Keep a company symbol library in `data/symbols/`. Every job in that data folder sees the same library. Each symbol has a name, a category, a default tag, a default assembly, and an example image. Starter symbols are drawn for this tool. Add another by boxing one symbol on any job. Nothing from a vendor symbol library is included.
- Find that symbol across the set. Box one mark on a sheet, or pick one from the library, and set a similarity cutoff from 0 to 1. The comparison tries rotation in 45-degree steps, including the 90-degree turns, and the mirror of each rotation. Every match becomes a traceable item with its score and confidence band and goes through review. A score below the high cutoff (0.90) is not accepted and does not count until you accept it.
- Flag a mark that is on the plan and missing from the legend. The review page lists it as an RFI so it can be sent out. The mark stays uncounted until a legend or an example match gives it a type.

The legend is the stack of labeled symbols under the heading `SYMBOL LEGEND`. Rows more than 72 points apart are not treated as the same legend. Match lines need a shared key and one overlap depth, for example `MATCH LINE ML-A OVERLAP 120`.

## What v1 does not do

- Conduit routing, wire sizing, voltage drop, derating, home-run grouping, or Revit / BIM output.
- Invented or eyeballed counts, guessed panel or circuit links, a price book, or default labor hours.
- A text-tag detector. The count method can record `text tag` when a future detector emits one. This version does not.
- Replacing an assembly allowance with a measured length. A measurement is its own line. It does not become the raceway or conductor quantity.
- Conduit routing and home runs. The measure tool is a polyline length only. A revision diff does not create either one.
- A rotated sheet, or any alignment that does not settle inside the search window. Those sheets are refused.
- Replacing the previous PDF in place without an uploaded revision. The Revision page is what stores the compare.
- A schedule value that the drawing did not print. A blank cell stays blank.
- A confidence flag with no detector score. Blank means no score. It is not filled in.
- A default markup.
- A multi-contractor product.
- Designing the fire alarm system, programming lighting or shade controls, or replacing the vendor's shop drawings. The count is by the printed symbol and the job's scope flag.
- A device count from OCR text. OCR reads words. A text search hit does not add a takeoff item. Symbol search is a separate step, and it compares a silhouette. It does not read a manufacturer's symbol library.
- A scale or a schedule cell from low-confidence OCR. That reading stays unset and is listed to check. The same cutoff applies whether the words came from Tesseract or from Google Cloud Vision.
- Fitting a detector from the benchmark. The harness scores the current takeoff. It does not train a model.
- Scoring saved example-search rows on a fresh benchmark run. `benchmark` reads the PDF and runs takeoff again. An accepted example-search row in exported truth is a miss on that run when the legend did not find the same mark.
- Choosing a note disposition for you. The estimator picks priced/included, excluded, clarification, an add or deduct alternate, RFI, or not applicable. Keyword hints cover the notes that changed the first real bid (empty telco/CATV PVC, low-voltage prewire, Romex with owner approval, a warranty term, field changes at no extra charge, and connecting other trades). They are hints, not a specification reader.
- Filing an RFI with the GC. The Excel sheet and the `.docx` are the log. The `.docx` carries the company name and no street address.
- Tracing a feeder graphic. The one-line comparison is panel names printed under a one-line or riser heading against the panel schedule names. A title-block scale is compared with a scale printed in the view. A missing scale stays on the scale page.

## Routing seam

`adel_takeoff/routing/protocol.py` defines `ConduitRouter`. The only implementation is `UnimplementedConduitRouter`, which raises `NotImplementedError` and produces no route. The count path does not import or call it. Routing is not implemented.

## Install and run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Scanned sheets also need the Tesseract program (`tesseract` on PATH). Vector PDFs do not. Set `GOOGLE_VISION_API_KEY` to use Google Cloud Vision instead of Tesseract. With the variable unset, Tesseract stays the reader. Low-confidence words are withheld the same way either way.

```bash
python -m adel_takeoff
```

Open http://127.0.0.1:8000

Jobs are stored under `./data`. Review decisions are `data/reviews/<job-id>.json`. Assembly overrides are `data/assemblies/<job-id>.json`. The starter assemblies are `assemblies/starter.json`. Manual scales and measured lengths are `data/scales/<job-id>.json`. A revision compare is `data/revisions/<job-id>.json`, and the overlay PDF is `data/revisions/<job-id>-overlay.pdf`. The text index is `data/text/<job-id>.json`. Search from the job's Search page. The Excel download includes the Text search sheet and any OCR checks. The company symbol library is `data/symbols/`. Open a job's Symbols page to add a boxed symbol or to search the set. A match below the high confidence cutoff waits on the review page.

To compare a symbol, upload the set, open Symbols, pick a library symbol or enter the box around one mark, and set the cutoff. Find matches. Accept or reject each new row on Review counts. A mark the legend does not name is already listed there as an RFI.

To compare a revision, upload the previous set first, open that job, choose Revision, and upload the revised PDF. The job then becomes the revised set. Unchanged review decisions stay. Changed marks return to check. Download Excel for the Revision Changes sheet, or open the overlay PDF from the revision page.

The same actions are available from the command line:

```bash
python -m adel_takeoff takeoff drawing.pdf
python -m adel_takeoff review JOB_ID --item 3 --action reject
python -m adel_takeoff review JOB_ID --page 1 --x 420 --y 180 --label "DUPLEX RECEPTACLE"
python -m adel_takeoff export JOB_ID --out .
python -m adel_takeoff benchmark benchmarks/synthetic-scheduled
python -m adel_takeoff truth JOB_ID --out ~/adel-benchmarks/job-name
```

## Notes, keynotes, and the RFI log

Numbered lines under `GENERAL NOTES` or `KEYNOTES` become notes with a sheet and a point. The words can come from the PDF text layer or from OCR. A keynote tag such as `KN-1` is tied to that note and to the nearest mark within 72 points.

Open the job's Notes page and set a disposition on every numbered general note. Until you do, the Excel export carries a warning and the Notes Disposition sheet lists the open notes.

The Coordination page is one log. It gathers unlegended symbols, tags with no schedule row, plan-versus-schedule differences, a panel on the one-line or riser with no matching panel schedule, a title-block scale that disagrees with the view, a panel header circuit count that disagrees with the drawn circuits, notes you marked RFI, and rows you add by hand. Columns are id, sheet, location, item, question, why it matters, who answers (Engineer, Architect, ID, GC, or Owner), a suggested pricing assumption, and status.

Download the same log from Excel (`RFI log`) or from `Download RFI log` (a `.docx` with no street address).

```bash
python -m adel_takeoff
```

Then open `/jobs/<id>/notes`, `/jobs/<id>/coordination`, `/jobs/<id>/export.xlsx`, and `/jobs/<id>/rfi.docx`.

## Zones

Open a job's Zones page and draw or type a rectangle or a polygon on one sheet. Each zone has a name and a type: alternate, phase, floor, area, or by others / NIC. An alternate also has a side, add or deduct. The corners are PDF points. Clicking the sheet records them.

A counted mark is assigned to every zone that contains the center of its mark on that sheet. `Level 2` on E-2 and `Level 2` on E-3 are one zone in the rollup. A mark that sits in two zones of the same type on the same point is flagged for review and assigned to neither of them. A floor and a phase can both apply. A by others / NIC zone stays on the takeoff and is left out of the base total. An alternate's quantity is an add or deduct line, not part of the base.

The Excel report adds a Zone column on Quantities and a Zones sheet with the zone list and the rollup by zone and system. The marked-up PDF draws each zone outline and its name.

```bash
python -m adel_takeoff
```

Then open `/jobs/<id>/zones`, save a zone, and open Review counts, the Excel download, and the marked-up PDF.

Zones are not detected from the drawing. The estimator names them. An overlap is flagged rather than resolved. There is no routing.

## Accuracy benchmark

A set folder holds the drawing and the expected result:

```text
<set-dir>/
  drawing.pdf
  thresholds.json
  truth/sheets/<sheet>.json
  truth/totals/<tag>.json
```

`drawing.pdf` is the plan set. `set.pdf`, or the only PDF in the folder, is accepted too. Each sheet file lists the devices that should be found:

```json
{
  "sheet": "E1",
  "tolerance_pt": 18,
  "items": [
    {
      "tag": "DUPLEX RECEPTACLE",
      "category": "power",
      "x": 150.0,
      "y": 160.0,
      "schedule": {"panel": "LP1", "circuit": "12"}
    }
  ]
}
```

`x` and `y` are PDF points. A match is the same tag on that sheet, inside `tolerance_pt` (an item can set its own radius). `schedule` lists the printed values to check. Omitted fields are not checked. Recognized fields are panel, circuit, type mark, voltage, mounting, and mounting height.

When only the count is known, skip the sheet files and add one totals file per tag:

```json
{ "tag": "DUPLEX RECEPTACLE", "category": "power", "quantity": 3 }
```

`thresholds.json` is optional. When it is present, the command writes the scorecard and then exits 1 if precision, recall, schedule recall, or the largest absolute count error misses the bar. That is what the pytest run uses, so a regression fails CI.

```bash
python -m adel_takeoff benchmark path/to/set
```

The scorecard is `path/to/set/scorecard/scorecard.md` and `scorecard.xlsx`. Pass `--out` to write them somewhere else. The Excel sheets are Summary, By tag, By sheet, By category, Totals, Missed, Extra, and Schedule. Precision, recall, and count error are reported per tag, sheet, and category when locations are known. Missed and extra rows include the sheet and the point. A mark with no tag is listed as unmatched and is left out of precision.

Two synthetic sets live in the repo: `benchmarks/synthetic-scheduled` (points, schedule values, and totals) and `benchmarks/synthetic-totals` (totals only). Their drawings are generated for the tests. Real client drawings stay outside the repo. Point the same command at a folder on the estimator's machine:

```bash
python -m adel_takeoff benchmark ~/adel-benchmarks/riverside-school
```

Nothing in that folder is committed. Scorecard output under `benchmarks/**/scorecard/` is gitignored so a local run does not land in git either.

### Build truth from a reviewed job

Review the job, then export the decisions that should count:

```bash
python -m adel_takeoff truth JOB_ID --out ~/adel-benchmarks/riverside-school
```

Accepted marks, corrected tags, and added marks are written as `truth/sheets` and `truth/totals`. The job PDF is copied to `drawing.pdf` when that file is not already there. The review page link "Download benchmark truth" writes the same JSON, plus the drawing, as a zip. Rejected marks, pending example matches, excluded marks, and unmatched marks without a corrected tag stay out. That folder is then a benchmark set. Run `benchmark` on it the same way as the synthetic sets.

## Test

```bash
pytest
```

Tests draw their own PDFs. No outside drawing files are required.
