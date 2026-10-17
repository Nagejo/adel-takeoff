# AI Electrical Takeoff: Competitor Research for Adel Estimating
Researched Oct 17, 2026 (ET). Public vendor pages, help docs, press, and one public demo video only. No logins, no sign-ups.

## Name check
- "Aginera" is **Aginera** (aginera.ai), an AI takeoff and estimating platform. The spelling is correct.
- "Groundbook" is **Groundbook AI** (groundbook.ai), an AI plan review tool with a separate quantity takeoff service.
- Bluebeam: **Bluebeam Max** (AI add-on for Revu) is real. Its FAQ says it can count text-based items but does not reliably identify electrical symbols yet, so it is left out of the list below. Source: https://community.bluebeam.com/kb/bluebeam-max-faqs
- Attentive.ai is the company behind **Beam AI**. They are one product, not two.

## 1. Products

**Drawer AI**: made only for electrical work. https://drawer.ai/ · https://drawer.ai/pricing · https://drawer.ai/about-us
- Reads the symbol legend and the light fixture schedule. Detects power devices, fixtures and panel schedules. Links tags (panel, circuit, mounting height) to each device. Has a "Takeoff Pad" with QA tools.
- Sorts sheet types (plans, schedules, legends, risers) and strips out background, clouds and grids before detection.
- Groups circuits, places home-run boxes, routes branches, sizes wire with voltage drop and derating, and stitches sheets together using column grids. Exports to Excel and marked-up PDF.
- Roadmap: smart text search and keynote detection (Q3/Q4), and a company symbol library plus spec/contract-reading agents (2027).

**Aginera**: AI takeoff plus assembly-based estimating. https://aginera.ai/docs/takeoff/electrical · https://aginera.ai/solutions/electrical-estimating · https://aginera.ai/mep-ai-benchmark
- Takes PDF or DWG/DXF. Covers power, lighting, low-voltage and fire alarm sheets, panels and feeders. Places color-coded markers on the plan and builds per-sheet schedules.
- Review Extraction lets you accept, reject or correct items, each with a confidence score. You can reject existing-to-remain and other non-scope symbols.
- Has 28 assemblies that expand each symbol into its parts. Example: a duplex becomes the device, plate, 4" box, 25 LF of 3/4" EMT, 75 LF of #12, and 0.75 labor hours.
- Exports to Excel/CSV and to McCormick, Accubid, ConEst and Vision InfoSoft formats. Publishes per-trade accuracy results from a "zero-LLM" scorecard.

**Groundbook AI**: plan review plus a takeoff service. https://groundbook.ai/quantity-takeoff/ · https://groundbook.ai/for/contractors/
- Puts a numbered box on every counted symbol. "How we counted" shows the arithmetic, and notes flag where the drawings contradict each other. Quantities come in EA/LF/SF with confidence levels. Exports to PDF or CSV.
- Can show your own takeoff next to theirs. Plan review checks panel schedules against one-lines, equipment connections and clearances. Its "Smart Overlay" lines up two sheets by grid lines and columns.
- Pricing is in credits: 0.1 credit per page per trade. https://groundbook.ai/pricing/

**Togal.AI**: general takeoff with electrical tools. https://www.togal.ai/trades/electrical · https://www.togal.ai/features
- Image Search: draw a box around one symbol and find matches. Text Search finds fixture tags and panel names across the whole set. There is also Pattern Search and a "chat with your plans" feature.
- A public demo says image search could not yet run across the whole set and linear runs were manual: https://www.youtube.com/watch?v=vsnRqiupk1A
- Togal says conductor sizing, voltage drop and NEC review stay with the estimator.

**Beam AI (Attentive.ai)**: mostly a done-for-you service. https://www.ibeam.ai/subcontractors/electrical · https://www.ibeam.ai/pricing
- Electrical is only available as done-for-you: AI takeoff checked by a human QA team, delivered in 24–72 hours as Excel. Self-serve covers only HVAC, plumbing and steel.
- Reads specs, notes and legends. Detects addenda and changes between versions and produces variance reports.

**Trimble LiveCount (in Accubid)**: https://www.trimble.com/en/blog/construction/article/powerful-new-automated-takeoff-capabilities-in-trimble-livecount-digital-takeoff-software
- AI auto-count for set types only: 6 receptacle types and 5 switch/sensor types. You choose which sheets it runs on, then review in an Auto Count panel with an "Add Missed Symbols" toggle.
- Detects drawing scale automatically and asks you to set it by hand if it can't.
- Batch drawing versioning copies annotations to new versions and color-codes the changes.

**Kreo**: https://help-takeoff.kreo.net/en/articles/5481213-auto-count · https://www.kreo.net/trades/electrical-estimating-software
- Auto Count works from an example. You can erase clutter from the reference, allow rotated or mirrored matches, limit the search area, and see a similarity percentage for each hit with a slider to set the cutoff.
- "Split by Text" separates identical symbols by their label. "Save for next time" reuses a symbol on other pages. It can also detect scale, overlay or compare pages, and filter drawing styles.

**ConEst SureCount / IntelliBid** (not AI in the ML sense; its auto count uses pixel-pattern matching): https://conest.com/electrical-takeoff-software/
- Patented auto count that handles all symbol types at once.
- Area selection for alternates and zones. Overlay shows revision changes in green (new) and red (deleted). Material and labor are assigned during takeoff and flow into IntelliBid.

**McCormick Systems**: https://www.mccormicksys.com/blog/how-mccormicks-electrical-estimating-features-work/ · https://www.mccormicksys.com/ai-statement/
- Patented Auto Home Run (U.S. Patent 11,120,171) draws and measures wire and conduit runs. Auto-counts repeated symbols. Pulls labor units and supplier pricing during takeoff.
- AI symbol detection for common devices was on the 2026 roadmap. I found no public confirmation that it has shipped.

## 2. Features worth building into Adel Estimating
Ranked by value for a small Florida EC doing luxury residential and commercial work. "Planned" means it is already in the adel-takeoff pipeline.

| # | Ability | Status | Difficulty | Seen in |
|---|---|---|---|---|
| 1 | Every count traceable: numbered mark on the sheet, sheet/location ref on each Excel line, plus "how counted" arithmetic and a confidence flag | Planned (marked-up PDF); the per-line refs and audit trail are new | Low | Groundbook, Aginera |
| 2 | Review workflow: accept, reject or correct; add missed symbols; exclude existing-to-remain, NIC and "by others" before export | New | Med | Aginera, Trimble, Drawer QA |
| 3 | Read the light fixture schedule and panel schedules; link tags (type, panel, circuit, mounting height) to each device | Partly planned (legend and panel/circuit linking); fixture schedule parsing and mounting heights are new | Med | Drawer, Aginera |
| 4 | Assembly expansion: device to box, plate, raceway allowance, conductor and labor hours, with editable assemblies | New | Med | Aginera, McCormick, ConEst |
| 5 | Detect drawing scale automatically, and refuse to give lengths when it can't | New (needed before routing) | Low–Med | Trimble, Kreo |
| 6 | Search text and tags across the whole set (fixture tags, panel names) | New | Low | Togal, Drawer (roadmap), Kreo |
| 7 | Compare revisions and addenda: overlay with green/red changes, count deltas, carry review decisions to the new version | New | Med–High | ConEst, Trimble, Beam, Kreo |
| 8 | Low-voltage, fire alarm, controls and keypad device categories (big on luxury residential: AV, shades, security) | New | Med | Aginera |
| 9 | Company symbol library plus search-by-example for symbols missing from the legend (handles rotation and mirroring, user sets the similarity cutoff) | New | Med | Kreo, Togal, Drawer (2027), ConEst |
| 10 | Zone and area selection for alternates, phases or floors | New | Low–Med | ConEst |
| 11 | Discrepancy notes: device count on plan vs schedule, panel schedule vs one-line | New | Med | Groundbook |
| 12 | Read keynotes and general notes and link them to items | New | Med | Drawer (roadmap), Beam |
| 13 | Internal accuracy benchmark: ground-truth plan sets plus a deterministic scorecard run as regression tests | New | Low–Med | Aginera |
| 14 | Sheet classification (plans, schedules, legends, risers) and background/cloud cleanup before detection | New | Med | Drawer |
| 15 | Export to estimating-system formats (Accubid, McCormick, ConEst) | New (format specs may not be public) | Med | Aginera |
| 16 | Show the estimator's own takeoff next to the engine's numbers | New | Low | Groundbook |
| 17 | Stitch multiple sheets (by column grid) without double counting | Planned | High | Drawer, Groundbook overlay |
| 18 | Branch routing, home-run grouping and box placement, wire sizing, voltage drop, derating | Planned (later) | High | Drawer, McCormick |

Lower priority / skip for now: chat with plans (Togal), Revit/BIM output (Drawer), done-for-you human QA service (Beam).

Note: McCormick's Auto Home Run is patented (US 11,120,171). Do a quick IP check before building home-run automation (item 18).
