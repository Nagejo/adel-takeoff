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

The legend is the stack of labeled symbols under the heading `SYMBOL LEGEND`. Rows more than 72 points apart are not treated as the same legend. Match lines need a shared key and one overlap depth, for example `MATCH LINE ML-A OVERLAP 120`.

## What v1 does not do

- Conduit routing, wire sizing, voltage drop, derating, or Revit / BIM output.
- Invented or eyeballed counts, guessed panel or circuit links, a price book, or default labor hours.
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

Jobs are stored under `./data`.

## Test

```bash
pytest
```

Tests draw their own PDFs. No outside drawing files are required.
