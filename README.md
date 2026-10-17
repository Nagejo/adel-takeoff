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

The legend is the stack of labeled symbols under the heading `SYMBOL LEGEND`. Rows more than 72 points apart are not treated as the same legend. Match lines need a shared key and one overlap depth, for example `MATCH LINE ML-A OVERLAP 120`.

## What v1 does not do

- Conduit routing, wire sizing, voltage drop, derating, home-run grouping, or Revit / BIM output.
- Invented or eyeballed counts, guessed panel or circuit links, a price book, or default labor hours.
- A text-tag detector. The count method can record `text tag` when a future detector emits one. This version does not.
- A confidence flag with no detector score. Blank means no score. It is not filled in.
- A default markup.
- A multi-contractor product.

## Routing seam

`adel_takeoff/routing/protocol.py` defines `ConduitRouter`. The only implementation is `UnimplementedConduitRouter`, which raises `NotImplementedError` and produces no route. The count path does not import or call it. Routing is not implemented.

## Install and run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m adel_takeoff
```

Open http://127.0.0.1:8000

Jobs are stored under `./data`. Review decisions are `data/reviews/<job-id>.json`.

The same actions are available from the command line:

```bash
python -m adel_takeoff takeoff drawing.pdf
python -m adel_takeoff review JOB_ID --item 3 --action reject
python -m adel_takeoff review JOB_ID --page 1 --x 420 --y 180 --label "DUPLEX RECEPTACLE"
python -m adel_takeoff export JOB_ID --out .
```

## Test

```bash
pytest
```

Tests draw their own PDFs. No outside drawing files are required.
