"""Web app for an Adel Electric quantity takeoff."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from adel_takeoff.constants import (
    APPRENTICE_DEFAULT,
    CONTRACTOR,
    DISCLAIMER,
    FOREMAN_DEFAULT,
    JOURNEYMAN_DEFAULT,
    LABOR_RATES,
    PM_DEFAULT,
)
from adel_takeoff.assemblies import (
    AssemblyError,
    catalog_for_job,
    data_dir_for_job,
    load_overrides,
    load_starter,
    override_path,
    save_overrides,
    starter_catalog_path,
    submission_to_overrides,
)
from adel_takeoff.classify import SCOPE_DEFAULTS, SCOPE_FIELDS, posted_scope
from adel_takeoff.code_notes import edition_for
from adel_takeoff.costing import MarkupRejected, parse_markup, parse_rate
from adel_takeoff.db import (
    add_allowance,
    clear_takeoff,
    create_job,
    get_job,
    list_allowances,
    list_claims,
    list_items,
    list_rfis,
    money_text,
    save_takeoff,
    session,
    update_item_correction,
    update_item_review,
    update_settings,
)
from adel_takeoff.export_excel import build_workbook
from adel_takeoff.export_pdf import build_markup_pdf
from adel_takeoff.extract import page_words, read_sheet_title, sheet_name
from adel_takeoff.present import build_review
from adel_takeoff.scale import (
    PICKED_SCALES,
    ScaleInputError,
    calibrate,
    measure_polyline,
    parse_points,
    picked_scale,
    report_for_job,
    utc_now,
)
from adel_takeoff.scale_store import load_scales, save_scales, scale_path
from adel_takeoff.revision import (
    build_overlay,
    compare_sets,
    load_revision,
    overlay_file,
    revision_path,
    save_revision,
    utc_now as revision_now,
)
from adel_takeoff.review_store import (
    REVIEW_ACTIONS,
    ReviewError,
    add_missed_item,
    apply_decision,
    empty_document,
    load_document,
    overlay_items,
    review_path,
    save_document,
)
from adel_takeoff.takeoff import run_takeoff

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE / "templates"))


def data_root(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    return Path(os.environ.get("ADEL_DATA_DIR", "data"))


def db_path_for(data_dir: Path) -> Path:
    return Path(data_dir) / "jobs.sqlite"


def default_rates() -> dict:
    return {
        "journeyman": JOURNEYMAN_DEFAULT,
        "foreman": FOREMAN_DEFAULT,
        "pm": PM_DEFAULT,
        "apprentice": APPRENTICE_DEFAULT,
    }


def ingest_pdf(data_dir: Path, filename: str, payload: bytes) -> str:
    if not payload:
        raise ValueError("Upload a PDF drawing set")
    try:
        document = fitz.open(stream=payload, filetype="pdf")
        document.close()
    except Exception as exc:
        raise ValueError("Upload a PDF drawing set") from exc
    result = run_takeoff(payload)
    pdf_dir = Path(data_dir) / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    edition = edition_for(None)
    name = Path(filename or "drawing.pdf").name
    with session(db_path_for(data_dir)) as conn:
        job_id = create_job(
            conn,
            name=name,
            pdf_path="",
            rates=default_rates(),
            edition={"fbc": edition.fbc, "nec": edition.nec, "basis": edition.basis},
        )
        path = pdf_dir / f"{job_id}.pdf"
        path.write_bytes(payload)
        conn.execute("UPDATE jobs SET pdf_path = ? WHERE id = ?", (str(path), job_id))
        save_takeoff(conn, job_id, result)
    save_document(review_path(data_dir, job_id), empty_document())
    from adel_takeoff.text_index import build_text_index, save_text_index, text_path

    save_text_index(text_path(data_dir, job_id), build_text_index(payload))
    return job_id


def load_job(data_dir: Path, job_id: str) -> dict | None:
    with session(db_path_for(data_dir)) as conn:
        job = get_job(conn, job_id)
        if job is None:
            return None
        items = list_items(conn, job_id)
        allowances = list_allowances(conn, job_id)
        rfis = list_rfis(conn, job_id)
        claims = list_claims(conn, job_id)
    document = load_document(review_path(data_dir, job_id))
    return {
        "job": job,
        "items": overlay_items(items, document),
        "allowances": allowances,
        "rfis": rfis,
        "claims": claims,
    }


def sheet_choices(pdf_bytes: bytes) -> list[dict]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        choices = []
        for index, page in enumerate(document):
            words = page_words(page)
            choices.append(
                {
                    "page_index": index,
                    "sheet": sheet_name(words, index),
                    "sheet_title": read_sheet_title(words),
                    "width": float(page.rect.width),
                    "height": float(page.rect.height),
                }
            )
        return choices
    finally:
        document.close()


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = data_root(data_dir)
    db_path = db_path_for(root)
    app = FastAPI(title="Adel Electric quantity takeoff")
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    app.state.data_dir = root
    app.state.db_path = db_path

    def _load(job_id: str) -> dict:
        loaded = load_job(root, job_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return loaded

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"contractor": CONTRACTOR, "disclaimer": DISCLAIMER},
        )

    @app.post("/jobs")
    async def upload(pdf: UploadFile = File(...)):
        payload = await pdf.read()
        if not payload:
            raise HTTPException(status_code=400, detail="Upload a PDF drawing set")
        try:
            document = fitz.open(stream=payload, filetype="pdf")
            document.close()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Upload a PDF drawing set") from exc
        try:
            job_id = ingest_pdf(root, pdf.filename or "drawing.pdf", payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str, error: str = ""):
        loaded = _load(job_id)
        return TEMPLATES.TemplateResponse(
            request,
            "job.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "job": loaded["job"],
                "labor_rates": LABOR_RATES,
                "error": error,
                "scope_fields": SCOPE_FIELDS,
                "scope_defaults": SCOPE_DEFAULTS,
                "warning": build_review(
                    loaded["job"], loaded["items"], loaded["allowances"], loaded["rfis"], loaded["claims"]
                )["totals"]["journeyman_warning"],
            },
        )

    @app.post("/jobs/{job_id}")
    def save_job(
        request: Request,
        job_id: str,
        permit_issue_date: str = Form(""),
        markup: str = Form(""),
        rate_journeyman: str = Form(...),
        rate_foreman: str = Form(...),
        rate_pm: str = Form(...),
        rate_apprentice: str = Form(...),
        include_landscape: str = Form(""),
        include_lighting_control: str = Form(""),
        scope_power: str = Form(""),
        scope_lighting: str = Form(""),
        scope_landscape: str = Form(""),
        scope_lighting_control: str = Form(""),
        scope_low_voltage: str = Form(""),
        scope_fire_alarm: str = Form(""),
        scope_controls: str = Form(""),
    ):
        loaded = _load(job_id)
        try:
            parsed_markup = parse_markup(markup)
            rates = {
                "journeyman": parse_rate(rate_journeyman, "Journeyman"),
                "foreman": parse_rate(rate_foreman, "Foreman"),
                "pm": parse_rate(rate_pm, "PM or master"),
                "apprentice": parse_rate(rate_apprentice, "Apprentice"),
            }
            permit = _parse_date(permit_issue_date)
        except MarkupRejected as exc:
            return TEMPLATES.TemplateResponse(
                request,
                "job.html",
                {
                    "contractor": CONTRACTOR,
                    "disclaimer": DISCLAIMER,
                    "job": loaded["job"],
                    "labor_rates": LABOR_RATES,
                    "error": str(exc),
                    "scope_fields": SCOPE_FIELDS,
                    "scope_defaults": SCOPE_DEFAULTS,
                    "warning": build_review(
                        loaded["job"],
                        loaded["items"],
                        loaded["allowances"],
                        loaded["rfis"],
                        loaded["claims"],
                    )["totals"]["journeyman_warning"],
                },
                status_code=400,
            )
        edition = edition_for(permit)
        scopes = {
            "scope_power": posted_scope(scope_power, checkbox_yes=False, default="ec"),
            "scope_lighting": posted_scope(scope_lighting, checkbox_yes=False, default="ec"),
            "scope_landscape": posted_scope(
                scope_landscape,
                checkbox_yes=include_landscape == "yes",
                default="by_others",
            ),
            "scope_lighting_control": posted_scope(
                scope_lighting_control,
                checkbox_yes=include_lighting_control == "yes",
                default="by_others",
            ),
            "scope_low_voltage": posted_scope(scope_low_voltage, checkbox_yes=False, default="by_others"),
            "scope_fire_alarm": posted_scope(scope_fire_alarm, checkbox_yes=False, default="by_others"),
            "scope_controls": posted_scope(scope_controls, checkbox_yes=False, default="by_others"),
        }
        with session(db_path) as conn:
            update_settings(
                conn,
                job_id,
                {
                    "permit_issue_date": permit.isoformat() if permit else None,
                    "markup": format(parsed_markup, "f") if parsed_markup is not None else None,
                    "rate_journeyman": format(rates["journeyman"], "f"),
                    "rate_foreman": format(rates["foreman"], "f"),
                    "rate_pm": format(rates["pm"], "f"),
                    "rate_apprentice": format(rates["apprentice"], "f"),
                    "include_landscape": scopes["scope_landscape"] == "ec",
                    "include_lighting_control": scopes["scope_lighting_control"] == "ec",
                    "code_fbc": edition.fbc,
                    "code_nec": edition.nec,
                    "code_basis": edition.basis,
                    **scopes,
                },
            )
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

    def _assembly_page(request: Request, job_id: str, error: str = "", status_code: int = 200):
        loaded = _load(job_id)
        catalog = catalog_for_job(loaded["job"])
        display = [
            "duplex",
            "gfci",
            "switch",
            "dimmer",
            "recessed-fixture",
            "junction-box",
            "data-lv",
        ]
        order = {name: index for index, name in enumerate(display)}
        shown = sorted(catalog, key=lambda item: order.get(item.id, len(order)))
        review = build_review(
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
        data_dir = data_dir_for_job(loaded["job"]) or root
        return TEMPLATES.TemplateResponse(
            request,
            "assemblies.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "job": loaded["job"],
                "assemblies": shown,
                "labor_rates": LABOR_RATES,
                "starter_path": str(starter_catalog_path()),
                "override_path": str(override_path(data_dir, job_id)),
                "assembly_warning": review["assembly_warning"],
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/jobs/{job_id}/assemblies", response_class=HTMLResponse)
    def assemblies_page(request: Request, job_id: str):
        return _assembly_page(request, job_id)

    @app.post("/jobs/{job_id}/assemblies")
    async def save_assemblies(request: Request, job_id: str):
        loaded = _load(job_id)
        form = await request.form()
        starter = load_starter()
        submitted = {}
        for assembly in starter:
            if f"labor_{assembly.id}" not in form and f"qty_{assembly.id}_device" not in form and f"qty_{assembly.id}_box" not in form:
                continue
            parts = {}
            for part in assembly.parts:
                parts[part.role] = {
                    "quantity": form.get(f"qty_{assembly.id}_{part.role}", ""),
                    "size": form.get(f"size_{assembly.id}_{part.role}", ""),
                    "count": form.get(f"count_{assembly.id}_{part.role}", ""),
                }
            submitted[assembly.id] = {
                "confirmed": form.get(f"confirmed_{assembly.id}") == "yes",
                "labor_hours": form.get(f"labor_{assembly.id}", ""),
                "labor_rate_key": form.get(f"rate_{assembly.id}", assembly.labor_rate_key),
                "parts": parts,
            }
        data_dir = data_dir_for_job(loaded["job"])
        if data_dir is None:
            raise HTTPException(status_code=400, detail="This job has no data directory for assembly overrides.")
        path = override_path(data_dir, job_id)
        try:
            fresh = submission_to_overrides(starter, submitted)["overrides"]
            existing = load_overrides(path)["overrides"]
        except AssemblyError as exc:
            return _assembly_page(request, job_id, error=str(exc), status_code=400)
        merged = {key: value for key, value in existing.items() if key not in submitted}
        merged.update(fresh)
        save_overrides(path, {"overrides": merged})
        return RedirectResponse(url=f"/jobs/{job_id}/assemblies", status_code=303)

    def _scale_page(request: Request, job_id: str, error: str = "", status_code: int = 200):
        loaded = _load(job_id)
        report = report_for_job(loaded["job"])
        data_dir = data_dir_for_job(loaded["job"]) or root
        return TEMPLATES.TemplateResponse(
            request,
            "scale.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "job": loaded["job"],
                "sheets": report["sheets"],
                "measurements": report["measurements"],
                "picked_scales": PICKED_SCALES,
                "scale_path": str(scale_path(data_dir, job_id)),
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/jobs/{job_id}/scale", response_class=HTMLResponse)
    def scale_page(request: Request, job_id: str):
        return _scale_page(request, job_id)

    @app.post("/jobs/{job_id}/scales")
    async def save_scale(request: Request, job_id: str):
        loaded = _load(job_id)
        form = await request.form()
        data_dir = data_dir_for_job(loaded["job"])
        if data_dir is None:
            raise HTTPException(status_code=400, detail="This job has no data directory for scales.")
        path = scale_path(data_dir, job_id)
        document = load_scales(path)
        try:
            page_index = int(str(form.get("page_index") or "").strip())
        except ValueError:
            return _scale_page(request, job_id, error="Choose a sheet.", status_code=400)
        mode = "clear" if form.get("clear") == "yes" else str(form.get("mode") or "picked")
        if mode == "clear":
            document["manual"].pop(str(page_index), None)
            save_scales(path, document)
            return RedirectResponse(url=f"/jobs/{job_id}/scale", status_code=303)
        set_by = str(form.get("set_by") or "").strip()
        if not set_by:
            return _scale_page(request, job_id, error="Who set the scale is required.", status_code=400)
        try:
            if mode == "dimension":
                x1 = Decimal(str(form.get("x1") or "").strip())
                y1 = Decimal(str(form.get("y1") or "").strip())
                x2 = Decimal(str(form.get("x2") or "").strip())
                y2 = Decimal(str(form.get("y2") or "").strip())
                known = Decimal(str(form.get("known_feet") or "").strip())
                feet_per_point, label = calibrate(x1, y1, x2, y2, known)
                distance_pt = known / feet_per_point
                read_from = [
                    {
                        "kind": "known dimension",
                        "label": label,
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                        "nts": False,
                        "conflict": False,
                    }
                ]
                method = "known dimension"
            else:
                parsed = picked_scale(str(form.get("picked") or ""))
                feet_per_point = parsed.feet_per_point
                label = parsed.label
                read_from = [{"kind": "picked scale", "label": label, "bbox": None, "nts": False, "conflict": False}]
                method = "picked"
        except (InvalidOperation, ScaleInputError) as exc:
            message = str(exc) if isinstance(exc, ScaleInputError) else "The scale entry must be a number."
            return _scale_page(request, job_id, error=message, status_code=400)
        stored = {
            "label": label,
            "feet_per_point": format(feet_per_point, "f"),
            "method": method,
            "set_by": set_by,
            "set_at": utc_now(),
            "read_from": read_from,
        }
        if mode == "dimension":
            stored["known_feet"] = format(known, "f")
            stored["distance_pt"] = format(distance_pt, "f")
        document["manual"][str(page_index)] = stored
        save_scales(path, document)
        return RedirectResponse(url=f"/jobs/{job_id}/scale", status_code=303)

    @app.post("/jobs/{job_id}/measurements")
    async def save_measurement(request: Request, job_id: str):
        loaded = _load(job_id)
        form = await request.form()
        data_dir = data_dir_for_job(loaded["job"])
        if data_dir is None:
            raise HTTPException(status_code=400, detail="This job has no data directory for scales.")
        try:
            page_index = int(str(form.get("page_index") or "").strip())
            points = parse_points(str(form.get("points") or ""))
        except (ValueError, ScaleInputError) as exc:
            message = str(exc) if isinstance(exc, ScaleInputError) else "Choose a sheet."
            return _scale_page(request, job_id, error=message, status_code=400)
        set_by = str(form.get("set_by") or "").strip()
        if not set_by:
            return _scale_page(request, job_id, error="Who measured the length is required.", status_code=400)
        report = report_for_job(loaded["job"])
        sheet = next((item for item in report["sheets"] if item["page_index"] == page_index), None)
        if sheet is None:
            return _scale_page(request, job_id, error="That sheet is not in this job.", status_code=400)
        measured = measure_polyline(sheet, points)
        if not measured["ok"]:
            return _scale_page(request, job_id, error=measured["reason"], status_code=400)
        path = scale_path(data_dir, job_id)
        document = load_scales(path)
        document["measurements"].append(
            {
                "id": uuid4().hex[:12],
                "page_index": page_index,
                "sheet": sheet["sheet"],
                "sheet_title": sheet["sheet_title"],
                "points": [[format(point[0], "f"), format(point[1], "f")] for point in points],
                "length_ft": format(measured["length_ft"], "f"),
                "scale_label": measured["scale_label"],
                "scale_source": measured["scale_source"],
                "scale_read_from": measured["scale_read_from"],
                "method": "measured",
                "set_by": set_by,
                "set_at": utc_now(),
            }
        )
        save_scales(path, document)
        return RedirectResponse(url=f"/jobs/{job_id}/scale", status_code=303)

    def _revision_page(request: Request, job_id: str, error: str = "", status_code: int = 200):
        loaded = _load(job_id)
        data_dir = data_dir_for_job(loaded["job"]) or root
        report = load_revision(revision_path(data_dir, job_id))
        return TEMPLATES.TemplateResponse(
            request,
            "revision.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "job": loaded["job"],
                "revision": report,
                "error": error,
            },
            status_code=status_code,
        )

    @app.get("/jobs/{job_id}/revision", response_class=HTMLResponse)
    def revision_page(request: Request, job_id: str):
        return _revision_page(request, job_id)

    @app.post("/jobs/{job_id}/revision")
    async def compare_revision(request: Request, job_id: str):
        loaded = _load(job_id)
        form = await request.form()
        upload = form.get("pdf")
        payload = await upload.read() if upload is not None and hasattr(upload, "read") else b""
        filename = getattr(upload, "filename", "") or "revision.pdf"
        if not payload:
            return _revision_page(request, job_id, error="Upload the revised PDF.", status_code=400)
        try:
            document = fitz.open(stream=payload, filetype="pdf")
            document.close()
        except Exception:
            return _revision_page(request, job_id, error="Upload a PDF drawing set.", status_code=400)
        data_dir = data_dir_for_job(loaded["job"])
        if data_dir is None:
            raise HTTPException(status_code=400, detail="This job has no data directory for a revision.")
        previous_pdf = Path(loaded["job"]["pdf_path"]).read_bytes()
        review = load_document(review_path(data_dir, job_id))
        compared = compare_sets(previous_pdf, payload, loaded["items"], review.get("decisions") or {})
        overlay = build_overlay(previous_pdf, payload, compared["overlay_sheets"])
        overlay_path = overlay_file(data_dir, job_id)
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        overlay_path.write_bytes(overlay)
        Path(loaded["job"]["pdf_path"]).write_bytes(payload)
        with session(db_path) as conn:
            clear_takeoff(conn, job_id)
            save_takeoff(conn, job_id, compared["takeoff"])
            for item_id, decision in compared["kept_decisions"].items():
                update_item_review(conn, job_id, item_id, decision)
            for item_id, costs in compared["carried_costs"].items():
                update_item_correction(conn, job_id, item_id, costs)
        save_document(
            review_path(data_dir, job_id),
            {
                "added": [],
                "decisions": compared["kept_decisions"],
                "revision_checks": compared["revision_checks"],
            },
        )
        save_revision(
            revision_path(data_dir, job_id),
            {
                "compared": True,
                "note": "",
                "previous_name": loaded["job"].get("name") or "",
                "revised_name": Path(filename).name,
                "compared_at": revision_now(),
                "sheets": compared["sheets"],
                "changes": compared["changes"],
                "revision_checks": compared["revision_checks"],
            },
        )
        return RedirectResponse(url=f"/jobs/{job_id}/revision", status_code=303)

    @app.get("/jobs/{job_id}/revision/overlay.pdf")
    def revision_overlay(job_id: str):
        loaded = _load(job_id)
        data_dir = data_dir_for_job(loaded["job"]) or root
        path = overlay_file(data_dir, job_id)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No revision overlay has been made.")
        return Response(
            content=path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="revision-overlay.pdf"'},
        )

    @app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
    def review_page(request: Request, job_id: str):
        loaded = _load(job_id)
        review = build_review(
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
        pdf_path = Path(loaded["job"]["pdf_path"])
        return TEMPLATES.TemplateResponse(
            request,
            "review.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "review": review,
                "labor_rates": LABOR_RATES,
                "sheets": sheet_choices(pdf_path.read_bytes()),
                "review_actions": REVIEW_ACTIONS,
            },
        )

    @app.post("/jobs/{job_id}/items/{item_id}/correction")
    def correct_item(
        job_id: str,
        item_id: str,
        override_quantity: str = Form(""),
        material_unit_cost: str = Form(""),
        labor_hours: str = Form(""),
        labor_rate_key: str = Form("journeyman"),
    ):
        loaded = _load(job_id)
        if not any(item["id"] == item_id for item in loaded["items"]):
            raise HTTPException(status_code=404, detail="Item not found")
        if labor_rate_key not in {key for key, _label, _rate in LABOR_RATES}:
            raise HTTPException(status_code=400, detail="Unknown labor rate")
        try:
            override = _optional_int(override_quantity, "Count correction")
            material = _optional_decimal(material_unit_cost, "Material unit cost")
            hours = _optional_decimal(labor_hours, "Labor hours")
        except MarkupRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with session(db_path) as conn:
            update_item_correction(
                conn,
                job_id,
                item_id,
                {
                    "override_quantity": override,
                    "material_unit_cost": money_text(material),
                    "labor_hours": money_text(hours),
                    "labor_rate_key": labor_rate_key,
                },
            )
        return RedirectResponse(url=f"/jobs/{job_id}/review", status_code=303)

    @app.post("/jobs/{job_id}/items/{item_id}/review")
    def review_item(
        job_id: str,
        item_id: str,
        action: str = Form(...),
        label: str = Form(""),
        panel: str = Form(""),
        circuit: str = Form(""),
    ):
        _load(job_id)
        try:
            with session(db_path) as conn:
                apply_decision(conn, root, job_id, item_id, action, label, panel, circuit)
        except ReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return RedirectResponse(url=f"/jobs/{job_id}/review", status_code=303)

    @app.post("/jobs/{job_id}/items")
    def add_item(
        job_id: str,
        page_index: str = Form(...),
        x: str = Form(...),
        y: str = Form(...),
        label: str = Form(""),
        panel: str = Form(""),
        circuit: str = Form(""),
    ):
        loaded = _load(job_id)
        try:
            parsed_page = int(page_index)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="PDF page must be a whole number.") from exc
        pdf_bytes = Path(loaded["job"]["pdf_path"]).read_bytes()
        try:
            with session(db_path) as conn:
                add_missed_item(
                    conn,
                    root,
                    job_id,
                    pdf_bytes=pdf_bytes,
                    page_index=parsed_page,
                    x=x,
                    y=y,
                    label=label,
                    panel=panel,
                    circuit=circuit,
                )
        except ReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return RedirectResponse(url=f"/jobs/{job_id}/review", status_code=303)

    @app.post("/jobs/{job_id}/allowances")
    def create_allowance(
        job_id: str,
        description: str = Form(""),
        quantity: str = Form(""),
        reason: str = Form(""),
        category: str = Form("allowance"),
        material_unit_cost: str = Form(""),
        labor_hours: str = Form(""),
        labor_rate_key: str = Form("journeyman"),
    ):
        _load(job_id)
        description = description.strip()
        reason = reason.strip()
        if not description or not reason or quantity.strip() == "":
            raise HTTPException(
                status_code=400,
                detail="An allowance needs a description, a quantity, and a reason.",
            )
        try:
            parsed_quantity = Decimal(quantity.strip())
            if not parsed_quantity.is_finite() or parsed_quantity < 0:
                raise MarkupRejected("Allowance quantity must be zero or greater.")
            material = _optional_decimal(material_unit_cost, "Material unit cost")
            hours = _optional_decimal(labor_hours, "Labor hours")
        except (InvalidOperation, MarkupRejected) as exc:
            raise HTTPException(status_code=400, detail="Allowance quantity must be a number.") from exc
        if labor_rate_key not in {key for key, _label, _rate in LABOR_RATES}:
            raise HTTPException(status_code=400, detail="Unknown labor rate")
        with session(db_path) as conn:
            add_allowance(
                conn,
                job_id,
                {
                    "description": description,
                    "quantity": parsed_quantity,
                    "reason": reason,
                    "category": category.strip() or "allowance",
                    "material_unit_cost": money_text(material),
                    "labor_hours": money_text(hours),
                    "labor_rate_key": labor_rate_key,
                },
            )
        return RedirectResponse(url=f"/jobs/{job_id}/review", status_code=303)

    @app.get("/jobs/{job_id}/search", response_class=HTMLResponse)
    def search_page(request: Request, job_id: str, q: str = "", hit: int = -1):
        from adel_takeoff.text_index import index_for_job, save_text_index, search_index

        loaded = _load(job_id)
        index, path = index_for_job(loaded["job"])
        query = " ".join(q.split())
        hits = search_index(index, query) if query else []
        if path is not None and query:
            index["query"] = query
            index["hits"] = hits
            save_text_index(path, index)
        spot = hits[hit] if hits and 0 <= hit < len(hits) else None
        return TEMPLATES.TemplateResponse(
            request,
            "search.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "job": loaded["job"],
                "query": query,
                "pages": index.get("pages") or [],
                "hits": hits,
                "spot": spot,
                "hit_index": hit if spot else -1,
                "checks": index.get("ocr_checks") or [],
            },
        )

    @app.get("/jobs/{job_id}/search/spot.png")
    def search_spot(
        job_id: str,
        page: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ):
        from adel_takeoff.text_index import render_hit

        loaded = _load(job_id)
        pdf_path = Path(loaded["job"]["pdf_path"])
        try:
            payload = render_hit(pdf_path.read_bytes(), page, (x0, y0, x1, y1))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=payload, media_type="image/png")

    @app.get("/jobs/{job_id}/export.xlsx")
    def export_excel(job_id: str):
        loaded = _load(job_id)
        payload = build_workbook(
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="adel-quantity-report.xlsx"'},
        )

    @app.get("/jobs/{job_id}/export.pdf")
    def export_pdf(job_id: str):
        loaded = _load(job_id)
        pdf_path = Path(loaded["job"]["pdf_path"])
        payload = build_markup_pdf(
            pdf_path.read_bytes(),
            loaded["job"],
            loaded["items"],
            loaded["allowances"],
            loaded["rfis"],
            loaded["claims"],
        )
        return Response(
            content=payload,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="adel-quantity-markup.pdf"'},
        )

    return app


def _parse_date(raw: str) -> date | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise MarkupRejected("Permit-issue date must be YYYY-MM-DD.") from exc


def _optional_int(raw: str, label: str) -> int | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise MarkupRejected(f"{label} must be a whole number.") from exc
    if value < 0:
        raise MarkupRejected(f"{label} must be zero or greater.")
    return value


def _optional_decimal(raw: str, label: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MarkupRejected(f"{label} must be a number.") from exc
    if not value.is_finite() or value < 0:
        raise MarkupRejected(f"{label} must be zero or greater.")
    return value


app = create_app()
