"""Manual scales and measured lengths stored beside the job."""

from __future__ import annotations

import json
from pathlib import Path


def scale_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "scales" / f"{job_id}.json"


def empty_document() -> dict:
    return {"manual": {}, "measurements": []}


def load_scales(path: Path) -> dict:
    if not path.is_file():
        return empty_document()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_document()
    manual = data.get("manual") or {}
    measurements = data.get("measurements") or []
    if not isinstance(manual, dict) or not isinstance(measurements, list):
        return empty_document()
    return {"manual": manual, "measurements": measurements}


def save_scales(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "manual": document.get("manual") or {},
            "measurements": document.get("measurements") or [],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
