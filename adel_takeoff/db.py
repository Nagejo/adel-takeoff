"""Local SQLite storage so a review survives a refresh."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    permit_issue_date TEXT,
    markup TEXT,
    rate_journeyman TEXT NOT NULL,
    rate_foreman TEXT NOT NULL,
    rate_pm TEXT NOT NULL,
    rate_apprentice TEXT NOT NULL,
    include_landscape INTEGER NOT NULL DEFAULT 0,
    include_lighting_control INTEGER NOT NULL DEFAULT 0,
    code_fbc TEXT NOT NULL,
    code_nec TEXT NOT NULL,
    code_basis TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    label TEXT NOT NULL,
    category TEXT NOT NULL,
    measurement TEXT NOT NULL,
    measured_quantity INTEGER,
    sheets_json TEXT NOT NULL,
    panel TEXT NOT NULL DEFAULT '',
    circuit TEXT NOT NULL DEFAULT '',
    link_flag TEXT NOT NULL DEFAULT '',
    deduped INTEGER NOT NULL DEFAULT 0,
    occurrences_json TEXT NOT NULL,
    override_quantity INTEGER,
    material_unit_cost TEXT,
    labor_hours TEXT,
    labor_rate_key TEXT NOT NULL DEFAULT 'journeyman',
    method TEXT NOT NULL DEFAULT 'legend_match',
    match_score TEXT,
    review_status TEXT NOT NULL DEFAULT 'accepted',
    exclusion TEXT NOT NULL DEFAULT '',
    corrected_label TEXT,
    corrected_panel TEXT,
    corrected_circuit TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS allowances (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity TEXT NOT NULL,
    reason TEXT NOT NULL,
    category TEXT NOT NULL,
    material_unit_cost TEXT,
    labor_hours TEXT,
    labor_rate_key TEXT NOT NULL DEFAULT 'journeyman',
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS rfis (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    detail TEXT NOT NULL,
    sections TEXT NOT NULL DEFAULT '',
    disposition TEXT NOT NULL DEFAULT 'rfi',
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS code_claims (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    section TEXT NOT NULL,
    rendered TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
"""


_ITEM_COLUMNS = {
    "method": "TEXT NOT NULL DEFAULT 'legend_match'",
    "match_score": "TEXT",
    "review_status": "TEXT NOT NULL DEFAULT 'accepted'",
    "exclusion": "TEXT NOT NULL DEFAULT ''",
    "corrected_label": "TEXT",
    "corrected_panel": "TEXT",
    "corrected_circuit": "TEXT",
}


_JOB_SCOPE_COLUMNS = {
    "scope_power": "TEXT NOT NULL DEFAULT 'ec'",
    "scope_lighting": "TEXT NOT NULL DEFAULT 'ec'",
    "scope_landscape": "TEXT NOT NULL DEFAULT 'by_others'",
    "scope_lighting_control": "TEXT NOT NULL DEFAULT 'by_others'",
    "scope_low_voltage": "TEXT NOT NULL DEFAULT 'by_others'",
    "scope_fire_alarm": "TEXT NOT NULL DEFAULT 'by_others'",
    "scope_controls": "TEXT NOT NULL DEFAULT 'by_others'",
}


def _ensure_item_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, column_type in _ITEM_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {column_type}")


def _ensure_job_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    added_landscape = "scope_landscape" not in existing
    added_control = "scope_lighting_control" not in existing
    for name, column_type in _JOB_SCOPE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}")
    if added_landscape:
        conn.execute("UPDATE jobs SET scope_landscape = 'ec' WHERE include_landscape = 1")
    if added_control:
        conn.execute(
            "UPDATE jobs SET scope_lighting_control = 'ec' WHERE include_lighting_control = 1"
        )


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _ensure_item_columns(conn)
    _ensure_job_columns(conn)
    return conn


@contextmanager
def session(db_path: Path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _job_from_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["include_landscape"] = bool(data["include_landscape"])
    data["include_lighting_control"] = bool(data["include_lighting_control"])
    return data


def _item_from_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["sheets"] = json.loads(data.pop("sheets_json"))
    data["occurrences"] = json.loads(data.pop("occurrences_json"))
    data["deduped"] = bool(data["deduped"])
    return data


def create_job(conn, *, name: str, pdf_path: str, rates: dict, edition: dict) -> str:
    job_id = uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO jobs (
            id, name, created_at, pdf_path, permit_issue_date, markup,
            rate_journeyman, rate_foreman, rate_pm, rate_apprentice,
            include_landscape, include_lighting_control,
            code_fbc, code_nec, code_basis
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, 0, 0, ?, ?, ?)
        """,
        (
            job_id,
            name,
            datetime.now(timezone.utc).isoformat(),
            pdf_path,
            str(rates["journeyman"]),
            str(rates["foreman"]),
            str(rates["pm"]),
            str(rates["apprentice"]),
            edition["fbc"],
            edition["nec"],
            edition["basis"],
        ),
    )
    return job_id


def save_takeoff(conn, job_id: str, result) -> None:
    for item in result.items:
        conn.execute(
            """
            INSERT INTO items (
                id, job_id, label, category, measurement, measured_quantity,
                sheets_json, panel, circuit, link_flag, deduped, occurrences_json,
                labor_rate_key, method, match_score, review_status, exclusion
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'journeyman', ?, ?, 'accepted', '')
            """,
            (
                item.item_id,
                job_id,
                item.label,
                item.category,
                item.measurement,
                item.measured_quantity,
                json.dumps(item.sheets),
                item.panel,
                item.circuit,
                item.link_flag,
                1 if item.deduped else 0,
                json.dumps([occurrence.as_dict() for occurrence in item.occurrences]),
                item.method,
                score_text(item.match_score),
            ),
        )
    for rfi in result.rfis:
        conn.execute(
            """
            INSERT INTO rfis (id, job_id, topic, detail, sections, disposition)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uuid4().hex[:12], job_id, rfi.topic, rfi.detail, rfi.sections, rfi.disposition),
        )
    for claim in result.code_claims:
        conn.execute(
            """
            INSERT INTO code_claims (id, job_id, statement, section, rendered)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid4().hex[:12], job_id, claim.statement, claim.section, claim.rendered),
        )


def clear_takeoff(conn, job_id: str) -> None:
    conn.execute("DELETE FROM items WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM rfis WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM code_claims WHERE job_id = ?", (job_id,))


def get_job(conn, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_from_row(row) if row else None


def list_items(conn, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM items WHERE job_id = ? ORDER BY rowid", (job_id,)
    ).fetchall()
    return [_item_from_row(row) for row in rows]


def list_allowances(conn, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM allowances WHERE job_id = ? ORDER BY rowid", (job_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def list_rfis(conn, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM rfis WHERE job_id = ? ORDER BY rowid", (job_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def list_claims(conn, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM code_claims WHERE job_id = ? ORDER BY rowid", (job_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def update_settings(conn, job_id: str, fields: dict) -> None:
    conn.execute(
        """
        UPDATE jobs SET
            permit_issue_date = ?,
            markup = ?,
            rate_journeyman = ?,
            rate_foreman = ?,
            rate_pm = ?,
            rate_apprentice = ?,
            include_landscape = ?,
            include_lighting_control = ?,
            code_fbc = ?,
            code_nec = ?,
            code_basis = ?,
            scope_power = ?,
            scope_lighting = ?,
            scope_landscape = ?,
            scope_lighting_control = ?,
            scope_low_voltage = ?,
            scope_fire_alarm = ?,
            scope_controls = ?
        WHERE id = ?
        """,
        (
            fields["permit_issue_date"],
            fields["markup"],
            fields["rate_journeyman"],
            fields["rate_foreman"],
            fields["rate_pm"],
            fields["rate_apprentice"],
            1 if fields["include_landscape"] else 0,
            1 if fields["include_lighting_control"] else 0,
            fields["code_fbc"],
            fields["code_nec"],
            fields["code_basis"],
            fields["scope_power"],
            fields["scope_lighting"],
            fields["scope_landscape"],
            fields["scope_lighting_control"],
            fields["scope_low_voltage"],
            fields["scope_fire_alarm"],
            fields["scope_controls"],
            job_id,
        ),
    )


def update_item_correction(conn, job_id: str, item_id: str, fields: dict) -> None:
    conn.execute(
        """
        UPDATE items SET
            override_quantity = ?,
            material_unit_cost = ?,
            labor_hours = ?,
            labor_rate_key = ?
        WHERE id = ? AND job_id = ?
        """,
        (
            fields["override_quantity"],
            fields["material_unit_cost"],
            fields["labor_hours"],
            fields["labor_rate_key"],
            item_id,
            job_id,
        ),
    )


def add_allowance(conn, job_id: str, fields: dict) -> str:
    allowance_id = uuid4().hex[:12]
    conn.execute(
        """
        INSERT INTO allowances (
            id, job_id, description, quantity, reason, category,
            material_unit_cost, labor_hours, labor_rate_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            allowance_id,
            job_id,
            fields["description"],
            str(fields["quantity"]),
            fields["reason"],
            fields["category"],
            fields.get("material_unit_cost"),
            fields.get("labor_hours"),
            fields.get("labor_rate_key") or "journeyman",
        ),
    )
    return allowance_id


def money_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def score_text(value: float | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), "f")


def update_item_review(conn, job_id: str, item_id: str, decision: dict) -> None:
    conn.execute(
        """
        UPDATE items SET
            review_status = ?,
            exclusion = ?,
            corrected_label = ?,
            corrected_panel = ?,
            corrected_circuit = ?
        WHERE id = ? AND job_id = ?
        """,
        (
            decision["review_status"],
            decision["exclusion"],
            decision.get("corrected_label"),
            decision.get("corrected_panel"),
            decision.get("corrected_circuit"),
            item_id,
            job_id,
        ),
    )


def insert_manual_item(conn, job_id: str, item: dict) -> None:
    existing = conn.execute(
        "SELECT id FROM items WHERE id = ? AND job_id = ?",
        (item["id"], job_id),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO items (
            id, job_id, label, category, measurement, measured_quantity,
            sheets_json, panel, circuit, link_flag, deduped, occurrences_json,
            labor_rate_key, method, match_score, review_status, exclusion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'journeyman', ?, NULL, ?, ?)
        """,
        (
            item["id"],
            job_id,
            item["label"],
            item["category"],
            item["measurement"],
            item["measured_quantity"],
            json.dumps(item["sheets"]),
            item.get("panel") or "",
            item.get("circuit") or "",
            item.get("link_flag") or "",
            json.dumps(item["occurrences"]),
            item.get("method") or "manual_add",
            item.get("review_status") or "accepted",
            item.get("exclusion") or "",
        ),
    )
