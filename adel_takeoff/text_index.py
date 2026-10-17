"""Per-job text index for vector text and scanned sheets.

A page with no letter or digit in its PDF text layer is read with Tesseract.
Each word keeps its box and confidence. Vector pages keep the PDF text.
Search looks across the stored index. Low-confidence OCR is recorded when a
scale, legend label, or schedule value is withheld.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz

from adel_takeoff.constants import OCR_DPI, OCR_PSM
from adel_takeoff.extract import Word, group_lines, legend_entries, read_pdf


_CACHE: dict[str, list[Word]] = {}


def text_path(data_dir: Path, job_id: str) -> Path:
    return Path(data_dir) / "text" / f"{job_id}.json"


def empty_index() -> dict:
    return {"pages": [], "ocr_checks": [], "query": "", "hits": []}


def load_text_index(path: Path) -> dict:
    if not path.is_file():
        return empty_index()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_index()
    pages = data.get("pages") or []
    checks = data.get("ocr_checks") or []
    hits = data.get("hits") or []
    if not isinstance(pages, list) or not isinstance(checks, list) or not isinstance(hits, list):
        return empty_index()
    return {
        "pages": pages,
        "ocr_checks": checks,
        "query": str(data.get("query") or ""),
        "hits": hits,
    }


def save_text_index(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "pages": document.get("pages") or [],
            "ocr_checks": document.get("ocr_checks") or [],
            "query": document.get("query") or "",
            "hits": document.get("hits") or [],
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _cache_key(page) -> str:
    digest = hashlib.sha256()
    images = page.get_images()
    if images:
        for image in images:
            xref = image[0]
            try:
                payload = page.parent.extract_image(xref)["image"]
            except Exception:
                payload = b""
            digest.update(payload)
    else:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), colorspace=fitz.csGRAY, alpha=False)
        digest.update(pixmap.samples)
    digest.update(f"{page.rect.width:.2f}x{page.rect.height:.2f}:{page.number}".encode())
    return digest.hexdigest()


def ocr_page_words(page) -> list[Word]:
    key = _cache_key(page)
    cached = _CACHE.get(key)
    if cached is not None:
        return list(cached)
    words = _recognize(page)
    _CACHE[key] = words
    return list(words)


def _recognize(page) -> list[Word]:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ValueError(
            "This page has no text layer. Install Tesseract, pytesseract, and Pillow to read it."
        ) from exc
    zoom = OCR_DPI / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    try:
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config=f"--psm {OCR_PSM}",
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise ValueError(
            "This page has no text layer. Install the Tesseract OCR program to read it."
        ) from exc
    scale = 72 / OCR_DPI
    words: list[Word] = []
    for index, raw in enumerate(data["text"]):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            score = float(data["conf"][index])
        except (TypeError, ValueError):
            continue
        if score < 0:
            continue
        x0 = float(data["left"][index]) * scale
        y0 = float(data["top"][index]) * scale
        x1 = x0 + float(data["width"][index]) * scale
        y1 = y0 + float(data["height"][index]) * scale
        words.append(
            Word(x0, y0, x1, y1, text, source="ocr", confidence=round(score / 100, 4))
        )
    return words


def _word_record(word: Word) -> dict:
    return {
        "text": word.text,
        "x0": round(word.x0, 2),
        "y0": round(word.y0, 2),
        "x1": round(word.x1, 2),
        "y1": round(word.y1, 2),
        "source": word.source,
        "confidence": None if word.confidence is None else round(float(word.confidence), 4),
    }


def _check_box(check: dict) -> dict:
    return {
        "kind": check.get("kind") or "",
        "sheet": check.get("sheet") or "",
        "page_index": check.get("page_index"),
        "text": check.get("text") or "",
        "confidence": check.get("confidence"),
        "note": check.get("note") or "",
        "x0": check.get("x0"),
        "y0": check.get("y0"),
        "x1": check.get("x1"),
        "y1": check.get("y1"),
    }


def build_text_index(pdf_bytes: bytes) -> dict:
    pages = read_pdf(pdf_bytes)
    checks: list[dict] = []
    stored_pages = []
    for page in pages:
        legend_entries(page)
        checks.extend(_check_box(check) for check in page.ocr_checks)
        stored_pages.append(
            {
                "page_index": page.index,
                "width": page.width,
                "height": page.height,
                "sheet": page.sheet,
                "sheet_title": page.sheet_title,
                "source": page.text_source,
                "words": [_word_record(word) for word in page.words],
            }
        )
    from adel_takeoff.scale import assess_pdf
    from adel_takeoff.schedules import parse_schedules

    for sheet in assess_pdf(pdf_bytes):
        for region in sheet["regions"]:
            if region.get("status") != "check":
                continue
            box = None
            for item in region.get("read_from") or []:
                if item.get("bbox"):
                    box = item["bbox"]
                    break
            checks.append(
                {
                    "kind": "scale",
                    "sheet": sheet.get("sheet") or "",
                    "page_index": sheet.get("page_index"),
                    "text": region.get("label") or "",
                    "confidence": region.get("ocr_confidence"),
                    "note": region.get("reason") or "",
                    "x0": box[0] if box else None,
                    "y0": box[1] if box else None,
                    "x1": box[2] if box else None,
                    "y1": box[3] if box else None,
                }
            )
    parsed = parse_schedules(pages)
    checks.extend(_check_box(check) for check in parsed.ocr_checks)
    return {
        "pages": stored_pages,
        "ocr_checks": checks,
        "query": "",
        "hits": [],
    }


def _as_words(records: list[dict]) -> list[Word]:
    words = []
    for record in records:
        confidence = record.get("confidence")
        words.append(
            Word(
                float(record["x0"]),
                float(record["y0"]),
                float(record["x1"]),
                float(record["y1"]),
                str(record.get("text") or ""),
                source=str(record.get("source") or "pdf"),
                confidence=None if confidence is None else float(confidence),
            )
        )
    return words


def search_index(index: dict, query: str, limit: int = 200) -> list[dict]:
    """Find a tag, a panel name, or any text. Hits keep the sheet and the box."""

    needle = " ".join(str(query or "").upper().split())
    if not needle:
        return []
    hits: list[dict] = []
    seen: set[tuple] = set()
    for page in index.get("pages") or []:
        words = _as_words(page.get("words") or [])
        for line in group_lines(words):
            upper = [word.text.upper() for word in line]
            for start in range(len(line)):
                phrase_words: list[Word] = []
                matched = False
                for end in range(start, len(line)):
                    phrase_words.append(line[end])
                    phrase = " ".join(upper[start : end + 1])
                    if needle in phrase:
                        matched = True
                        break
                    if len(phrase) > len(needle) + 48:
                        break
                if not matched:
                    continue
                key = (
                    page.get("page_index"),
                    round(phrase_words[0].x0, 1),
                    round(phrase_words[0].y0, 1),
                    round(phrase_words[-1].x1, 1),
                )
                if key in seen:
                    continue
                seen.add(key)
                confidences = [
                    word.confidence for word in phrase_words if word.confidence is not None
                ]
                confidence = min(confidences) if confidences else None
                hits.append(
                    {
                        "sheet": page.get("sheet") or "",
                        "sheet_title": page.get("sheet_title") or "",
                        "page_index": page.get("page_index"),
                        "text": " ".join(word.text for word in phrase_words),
                        "x0": round(min(word.x0 for word in phrase_words), 2),
                        "y0": round(min(word.y0 for word in phrase_words), 2),
                        "x1": round(max(word.x1 for word in phrase_words), 2),
                        "y1": round(max(word.y1 for word in phrase_words), 2),
                        "source": page.get("source") or phrase_words[0].source,
                        "confidence": None if confidence is None else round(confidence, 4),
                    }
                )
                if len(hits) >= limit:
                    return hits
    return hits


def render_hit(pdf_bytes: bytes, page_index: int, bbox: tuple[float, float, float, float]) -> bytes:
    """Draw the hit on a copy of the page and return a PNG. The file is unchanged."""

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if page_index < 0 or page_index >= document.page_count:
            raise ValueError("That page is not in this drawing set.")
        page = document[page_index]
        rect = fitz.Rect(bbox[0] - 2, bbox[1] - 2, bbox[2] + 2, bbox[3] + 2)
        page.draw_rect(rect, color=(0.12, 0.28, 0.75), width=1.4)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()


def index_for_job(job: dict) -> tuple[dict, Path | None]:
    from adel_takeoff.assemblies import data_dir_for_job

    data_dir = data_dir_for_job(job)
    if data_dir is None or not job.get("id"):
        return empty_index(), None
    path = text_path(data_dir, str(job["id"]))
    if path.is_file():
        return load_text_index(path), path
    pdf = Path(str(job.get("pdf_path") or ""))
    if not pdf.is_file():
        return empty_index(), path
    document = build_text_index(pdf.read_bytes())
    save_text_index(path, document)
    return document, path
