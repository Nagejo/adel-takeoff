"""Expand a counted device into the parts on its assembly.

The starter file is an example the estimator owns and edits. Per-job
overrides live beside the job. Example hours are not added to direct cost
until that assembly is confirmed. Raceway and conductor lengths are
allowances. Nothing in this module is a published labor standard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from adel_takeoff.constants import (
    CONFIRMED_ASSEMBLY_NOTE,
    LABOR_RATES,
    UNCONFIRMED_ASSEMBLY_WARNING,
)


ROLES = ("device", "plate", "box", "raceway", "conductor", "misc")
RATE_KEYS = {key for key, _label, _amount in LABOR_RATES}


class AssemblyError(ValueError):
    """The assembly file has a value this job will not use."""


@dataclass(frozen=True)
class AssemblyPart:
    role: str
    description: str
    quantity: Decimal
    unit: str
    size: str = ""
    count: str = ""
    allowance: bool = False


@dataclass(frozen=True)
class Assembly:
    id: str
    name: str
    version: str
    matches: tuple[str, ...]
    example: bool
    confirmed: bool
    labor_hours: Decimal
    labor_rate_key: str
    parts: tuple[AssemblyPart, ...]


def starter_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assemblies" / "starter.json"


def override_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "assemblies" / f"{job_id}.json"


def data_dir_for_job(job: dict) -> Path | None:
    raw = job.get("pdf_path") or ""
    if not raw:
        return None
    path = Path(raw)
    if path.parent.name == "pdfs":
        return path.parent.parent
    return None


def _decimal(value, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise AssemblyError(f"{label} must be a number.")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise AssemblyError(f"{label} must be a number.") from exc
    if not number.is_finite() or number < 0:
        raise AssemblyError(f"{label} must be zero or greater.")
    return number


def _part(raw: dict, assembly_id: str) -> AssemblyPart:
    if not isinstance(raw, dict):
        raise AssemblyError(f"{assembly_id} has a part that is not an object.")
    role = str(raw.get("role") or "").strip()
    if role not in ROLES:
        raise AssemblyError(f"{assembly_id} has an unknown part {role or '(blank)'}.")
    description = str(raw.get("description") or "").strip()
    if not description:
        raise AssemblyError(f"{assembly_id} {role} needs a description.")
    unit = str(raw.get("unit") or "").strip()
    if not unit:
        raise AssemblyError(f"{assembly_id} {role} needs a unit.")
    allowance = bool(raw.get("allowance"))
    if role in ("raceway", "conductor") and not allowance:
        raise AssemblyError(f"{assembly_id} {role} must be marked as an allowance.")
    return AssemblyPart(
        role=role,
        description=description,
        quantity=_decimal(raw.get("quantity"), f"{assembly_id} {role} quantity"),
        unit=unit,
        size=str(raw.get("size") or "").strip(),
        count=str(raw.get("count") or "").strip(),
        allowance=allowance,
    )


def _assembly(raw: dict) -> Assembly:
    if not isinstance(raw, dict):
        raise AssemblyError("An assembly must be an object.")
    assembly_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not assembly_id or not name:
        raise AssemblyError("An assembly needs an id and a name.")
    matches = tuple(
        " ".join(str(item).lower().split())
        for item in (raw.get("matches") or [])
        if str(item).strip()
    )
    if not matches:
        raise AssemblyError(f"{assembly_id} needs at least one match word.")
    rate_key = str(raw.get("labor_rate_key") or "journeyman").strip()
    if rate_key not in RATE_KEYS:
        raise AssemblyError(f"{assembly_id} labor rate must be one of the job rates.")
    parts_raw = raw.get("parts") or []
    if not isinstance(parts_raw, list) or not parts_raw:
        raise AssemblyError(f"{assembly_id} needs at least one part.")
    parts = tuple(_part(item, assembly_id) for item in parts_raw)
    roles = [part.role for part in parts]
    if len(roles) != len(set(roles)):
        raise AssemblyError(f"{assembly_id} repeats a part.")
    return Assembly(
        id=assembly_id,
        name=name,
        version=str(raw.get("version") or "example-1").strip() or "example-1",
        matches=matches,
        example=True,
        confirmed=False,
        labor_hours=_decimal(raw.get("labor_hours"), f"{assembly_id} labor hours"),
        labor_rate_key=rate_key,
        parts=parts,
    )


def load_starter(path: Path | None = None) -> list[Assembly]:
    catalog_path = path or starter_catalog_path()
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"Could not read the assembly file {catalog_path}.") from exc
    rows = payload.get("assemblies") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AssemblyError("The assembly file needs an assemblies list.")
    assemblies = [_assembly(row) for row in rows]
    ids = [item.id for item in assemblies]
    if len(ids) != len(set(ids)):
        raise AssemblyError("Assembly ids must be unique.")
    return assemblies


def load_overrides(path: Path) -> dict:
    if not path.is_file():
        return {"overrides": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssemblyError(f"Could not read assembly overrides {path}.") from exc
    if not isinstance(payload, dict):
        raise AssemblyError("Assembly overrides must be an object.")
    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise AssemblyError("Assembly overrides must be an object.")
    return {"overrides": overrides}


def _changed_part(part: AssemblyPart, patch: dict) -> tuple[AssemblyPart, bool]:
    changed = False
    quantity = part.quantity
    size = part.size
    count = part.count
    if "quantity" in patch and str(patch.get("quantity") or "").strip() != "":
        quantity = _decimal(patch.get("quantity"), f"{part.role} quantity")
        changed = changed or quantity != part.quantity
    if "size" in patch:
        size = str(patch.get("size") or "").strip()
        changed = changed or size != part.size
    if "count" in patch:
        count = str(patch.get("count") or "").strip()
        changed = changed or count != part.count
    return replace(part, quantity=quantity, size=size, count=count), changed


def apply_overrides(assemblies: list[Assembly], document: dict) -> list[Assembly]:
    """Overlay job edits. A partial override leaves the other starter values."""

    table = document.get("overrides") or {}
    if not isinstance(table, dict):
        raise AssemblyError("Assembly overrides must be an object.")
    resolved = []
    for assembly in assemblies:
        patch = table.get(assembly.id) or {}
        if not isinstance(patch, dict):
            raise AssemblyError(f"{assembly.id} override must be an object.")
        changed = False
        labor = assembly.labor_hours
        if "labor_hours" in patch and str(patch.get("labor_hours") or "").strip() != "":
            labor = _decimal(patch.get("labor_hours"), f"{assembly.id} labor hours")
            changed = changed or labor != assembly.labor_hours
        rate_key = assembly.labor_rate_key
        if patch.get("labor_rate_key"):
            rate_key = str(patch["labor_rate_key"]).strip()
            if rate_key not in RATE_KEYS:
                raise AssemblyError(f"{assembly.id} labor rate must be one of the job rates.")
            changed = changed or rate_key != assembly.labor_rate_key
        part_patches = patch.get("parts") or {}
        if not isinstance(part_patches, dict):
            raise AssemblyError(f"{assembly.id} parts override must be an object.")
        parts = []
        for part in assembly.parts:
            updated, part_changed = _changed_part(part, part_patches.get(part.role) or {})
            parts.append(updated)
            changed = changed or part_changed
        confirmed = bool(patch.get("confirmed"))
        version = str(patch.get("version") or "").strip()
        if not version:
            version = f"{assembly.version}+job" if changed else assembly.version
        resolved.append(
            replace(
                assembly,
                version=version,
                example=not confirmed,
                confirmed=confirmed,
                labor_hours=labor,
                labor_rate_key=rate_key,
                parts=tuple(parts),
            )
        )
    return resolved


def catalog_for_job(job: dict) -> list[Assembly]:
    assemblies = load_starter()
    data_dir = data_dir_for_job(job)
    job_id = str(job.get("id") or "")
    if data_dir is None or not job_id:
        return assemblies
    return apply_overrides(assemblies, load_overrides(override_path(data_dir, job_id)))


def match_assembly(label: str, assemblies: list[Assembly]) -> Assembly | None:
    """First matching assembly wins. The starter file lists specific names first."""

    text = " ".join(str(label or "").lower().split())
    if not text:
        return None
    for assembly in assemblies:
        if any(needle in text for needle in assembly.matches):
            return assembly
    return None


def _rates(job: dict) -> dict[str, Decimal]:
    return {
        key: Decimal(str(job[f"rate_{key}"]))
        for key, _label, _amount in LABOR_RATES
    }


def apply_assemblies(rows: list[dict], job: dict, catalog: list[Assembly]) -> None:
    """Attach assembly identity to counted rows.

    Confirmed labor hours are added to direct cost. Example hours stay on the
    row for the labor rollup and are not priced.
    """

    rates = _rates(job)
    for row in rows:
        row["assembly_id"] = ""
        row["assembly_name"] = ""
        row["assembly_version"] = ""
        row["assembly_confirmed"] = False
        row["assembly_example"] = False
        row["assembly_hours_per_unit"] = None
        row["assembly_labor_hours"] = None
        row["assembly_rate_key"] = ""
        if row.get("effective") is None:
            continue
        assembly = match_assembly(row.get("label") or "", catalog)
        if assembly is None:
            continue
        hours = assembly.labor_hours * Decimal(row["effective"])
        row["assembly_id"] = assembly.id
        row["assembly_name"] = assembly.name
        row["assembly_version"] = assembly.version
        row["assembly_confirmed"] = assembly.confirmed
        row["assembly_example"] = not assembly.confirmed
        row["assembly_hours_per_unit"] = assembly.labor_hours
        row["assembly_labor_hours"] = hours
        row["assembly_rate_key"] = assembly.labor_rate_key
        if assembly.confirmed:
            extra = hours * rates[assembly.labor_rate_key]
            base = row["direct_cost"] if row["direct_cost"] is not None else Decimal("0")
            row["direct_cost"] = base + extra


def assembly_warning(lines: list[dict]) -> str:
    used = [line for line in lines if line.get("assembly_id")]
    if any(not line.get("assembly_confirmed") for line in used):
        return UNCONFIRMED_ASSEMBLY_WARNING
    if used:
        return CONFIRMED_ASSEMBLY_NOTE
    return ""


def material_rows(lines: list[dict], catalog: list[Assembly]) -> list[dict]:
    """Roll identical parts together and keep every source item id."""

    by_id = {assembly.id: assembly for assembly in catalog}
    buckets: dict[tuple, dict] = {}
    order: list[tuple] = []
    for line in lines:
        assembly = by_id.get(line.get("assembly_id") or "")
        if assembly is None or line.get("effective") is None:
            continue
        count = Decimal(line["effective"])
        for part in assembly.parts:
            key = (
                assembly.id,
                part.role,
                part.description,
                part.unit,
                part.size,
                part.count,
                part.allowance,
                bool(line.get("assembly_confirmed")),
            )
            if key not in buckets:
                order.append(key)
                buckets[key] = {
                    "assembly": assembly.name,
                    "assembly_version": line.get("assembly_version") or assembly.version,
                    "role": part.role,
                    "description": part.description,
                    "unit": part.unit,
                    "size": part.size,
                    "conductor_count": part.count,
                    "allowance": "allowance" if part.allowance else "",
                    "quantity": Decimal("0"),
                    "item_ids": [],
                    "confirmed": bool(line.get("assembly_confirmed")),
                }
            buckets[key]["quantity"] += count * part.quantity
            for item_id in line.get("item_ids") or []:
                if item_id not in buckets[key]["item_ids"]:
                    buckets[key]["item_ids"].append(item_id)
    return [buckets[key] for key in order]


def labor_rows(lines: list[dict], job: dict) -> list[dict]:
    rates = _rates(job)
    labels = {key: label for key, label, _amount in LABOR_RATES}
    rows = []
    for line in lines:
        if not line.get("assembly_id"):
            continue
        hours = line.get("assembly_labor_hours")
        rate_key = line.get("assembly_rate_key") or "journeyman"
        rate = rates[rate_key]
        confirmed = bool(line.get("assembly_confirmed"))
        rows.append(
            {
                "line_id": line["line_id"],
                "item_ids": list(line.get("item_ids") or []),
                "item": line["label"],
                "assembly": line.get("assembly_name") or "",
                "assembly_version": line.get("assembly_version") or "",
                "count": line.get("effective"),
                "hours_per_unit": line.get("assembly_hours_per_unit"),
                "hours": hours,
                "rate": labels[rate_key],
                "rate_amount": rate,
                "confirmed": confirmed,
                "direct_labor": hours * rate if confirmed and hours is not None else None,
            }
        )
    return rows


def submission_to_overrides(starter: list[Assembly], submitted: dict) -> dict:
    """Keep an override only when the estimator confirmed it or changed a number."""

    by_id = {assembly.id: assembly for assembly in starter}
    overrides = {}
    for assembly_id, fields in submitted.items():
        assembly = by_id.get(assembly_id)
        if assembly is None or not isinstance(fields, dict):
            continue
        confirmed = bool(fields.get("confirmed"))
        labor_text = str(fields.get("labor_hours") or "").strip()
        labor = _decimal(labor_text, f"{assembly.name} labor hours") if labor_text else assembly.labor_hours
        rate_key = str(fields.get("labor_rate_key") or assembly.labor_rate_key).strip()
        if rate_key not in RATE_KEYS:
            raise AssemblyError(f"{assembly.name} labor rate must be one of the job rates.")
        parts = {}
        changed = labor != assembly.labor_hours or rate_key != assembly.labor_rate_key
        incoming_parts = fields.get("parts") or {}
        for part in assembly.parts:
            patch = incoming_parts.get(part.role) or {}
            quantity_text = str(patch.get("quantity") or "").strip()
            quantity = _decimal(quantity_text, f"{assembly.name} {part.role} quantity") if quantity_text else part.quantity
            size = str(patch.get("size") if "size" in patch else part.size).strip()
            count = str(patch.get("count") if "count" in patch else part.count).strip()
            if quantity != part.quantity or size != part.size or count != part.count:
                changed = True
            parts[part.role] = {
                "quantity": format(quantity, "f"),
                "size": size,
                "count": count,
            }
        if not confirmed and not changed:
            continue
        overrides[assembly_id] = {
            "confirmed": confirmed,
            "labor_hours": format(labor, "f"),
            "labor_rate_key": rate_key,
            "parts": parts,
        }
    return {"overrides": overrides}


def save_overrides(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
