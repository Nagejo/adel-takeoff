"""Web app for an Adel Electric quantity takeoff."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
from adel_takeoff.code_notes import edition_for
from adel_takeoff.costing import MarkupRejected, parse_markup, parse_rate
from adel_takeoff.db import (
    add_allowance,
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
    update_settings,
)
from adel_takeoff.export_excel import build_workbook
from adel_takeoff.export_pdf import build_markup_pdf
from adel_takeoff.present import build_review
from adel_takeoff.takeoff import run_takeoff

PACKAGE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE / "templates"))


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = Path(data_dir) if data_dir is not None else Path(os.environ.get("ADEL_DATA_DIR", "data"))
    db_path = root / "jobs.sqlite"
    pdf_dir = root / "pdfs"
    app = FastAPI(title="Adel Electric quantity takeoff")
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    app.state.data_dir = root
    app.state.db_path = db_path
    app.state.pdf_dir = pdf_dir

    def _rates() -> dict:
        return {
            "journeyman": JOURNEYMAN_DEFAULT,
            "foreman": FOREMAN_DEFAULT,
            "pm": PM_DEFAULT,
            "apprentice": APPRENTICE_DEFAULT,
        }

    def _load(job_id: str) -> dict:
        with session(db_path) as conn:
            job = get_job(conn, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found")
            return {
                "job": job,
                "items": list_items(conn, job_id),
                "allowances": list_allowances(conn, job_id),
                "rfis": list_rfis(conn, job_id),
                "claims": list_claims(conn, job_id),
            }

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
        result = run_takeoff(payload)
        pdf_dir.mkdir(parents=True, exist_ok=True)
        name = Path(pdf.filename or "drawing.pdf").name
        edition = edition_for(None)
        with session(db_path) as conn:
            job_id = create_job(
                conn,
                name=name,
                pdf_path="",
                rates=_rates(),
                edition={"fbc": edition.fbc, "nec": edition.nec, "basis": edition.basis},
            )
            path = pdf_dir / f"{job_id}.pdf"
            path.write_bytes(payload)
            conn.execute("UPDATE jobs SET pdf_path = ? WHERE id = ?", (str(path), job_id))
            save_takeoff(conn, job_id, result)
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
                    "include_landscape": include_landscape == "yes",
                    "include_lighting_control": include_lighting_control == "yes",
                    "code_fbc": edition.fbc,
                    "code_nec": edition.nec,
                    "code_basis": edition.basis,
                },
            )
        return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)

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
        return TEMPLATES.TemplateResponse(
            request,
            "review.html",
            {
                "contractor": CONTRACTOR,
                "disclaimer": DISCLAIMER,
                "review": review,
                "labor_rates": LABOR_RATES,
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
