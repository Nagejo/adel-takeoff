"""OCR backends.

Tesseract is the default. Google Cloud Vision is used only when
GOOGLE_VISION_API_KEY is set. Both return a confidence from 0 to 1 so the
same low-confidence cutoff can withhold a scale or a schedule value.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass

from adel_takeoff.constants import OCR_PSM


VISION_URL = "https://vision.googleapis.com/v1/images:annotate"


@dataclass(frozen=True)
class OcrToken:
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float | None


def _confidence(value) -> float | None:
    """Map a backend score onto 0–1. A missing score stays missing."""

    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0:
        return None
    if score > 1:
        score = score / 100
    if score > 1:
        score = 1.0
    return round(score, 4)


class TesseractBackend:
    name = "tesseract"

    def recognize(self, image) -> list[OcrToken]:
        try:
            import pytesseract
        except ImportError as exc:
            raise ValueError(
                "This page has no text layer. Install Tesseract, pytesseract, and Pillow to read it."
            ) from exc
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
        tokens: list[OcrToken] = []
        for index, raw in enumerate(data["text"]):
            text = str(raw or "").strip()
            if not text:
                continue
            score = _confidence(data["conf"][index])
            if score is None:
                continue
            tokens.append(
                OcrToken(
                    text=text,
                    left=float(data["left"][index]),
                    top=float(data["top"][index]),
                    width=float(data["width"][index]),
                    height=float(data["height"][index]),
                    confidence=score,
                )
            )
        return tokens


class GoogleVisionBackend:
    """Document text detection through the Vision REST API.

    ``transport`` is the HTTP call. Tests pass a fake. Nothing in the test
    suite calls the network.
    """

    name = "google_vision"

    def __init__(self, api_key: str, transport=None) -> None:
        self.api_key = api_key.strip()
        self.transport = transport or post_vision

    def recognize(self, image) -> list[OcrToken]:
        if not self.api_key:
            raise ValueError("Google Cloud Vision needs GOOGLE_VISION_API_KEY.")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(buffer.getvalue()).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }
            ]
        }
        url = f"{VISION_URL}?key={self.api_key}"
        body = self.transport(url, payload, 30)
        return tokens_from_vision(body)


def post_vision(url: str, payload: dict, timeout: float):
    import httpx

    try:
        response = httpx.post(url, json=payload, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ValueError("Google Cloud Vision could not be reached.") from exc
    if response.status_code >= 400:
        raise ValueError("Google Cloud Vision rejected the page.")
    return response.json()


def tokens_from_vision(body: dict) -> list[OcrToken]:
    responses = body.get("responses") if isinstance(body, dict) else None
    if not responses:
        return []
    annotation = (responses[0] or {}).get("fullTextAnnotation") or {}
    tokens: list[OcrToken] = []
    for page in annotation.get("pages") or []:
        for block in page.get("blocks") or []:
            for paragraph in block.get("paragraphs") or []:
                for word in paragraph.get("words") or []:
                    text = "".join(
                        str(symbol.get("text") or "") for symbol in word.get("symbols") or []
                    ).strip()
                    if not text:
                        continue
                    box = _vertex_box(word.get("boundingBox") or {})
                    if box is None:
                        continue
                    left, top, right, bottom = box
                    score = word.get("confidence")
                    if score is None:
                        symbol_scores = [
                            symbol.get("confidence")
                            for symbol in word.get("symbols") or []
                            if symbol.get("confidence") is not None
                        ]
                        score = (
                            sum(float(value) for value in symbol_scores) / len(symbol_scores)
                            if symbol_scores
                            else None
                        )
                    tokens.append(
                        OcrToken(
                            text=text,
                            left=left,
                            top=top,
                            width=max(0.0, right - left),
                            height=max(0.0, bottom - top),
                            confidence=_confidence(score),
                        )
                    )
    return tokens


def _vertex_box(box: dict) -> tuple[float, float, float, float] | None:
    vertices = box.get("vertices") or box.get("normalizedVertices") or []
    points = []
    for vertex in vertices:
        if not isinstance(vertex, dict):
            continue
        points.append((float(vertex.get("x") or 0), float(vertex.get("y") or 0)))
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def select_backend(environ: dict | None = None, transport=None):
    """Vision when the API key is set. Tesseract otherwise."""

    env = os.environ if environ is None else environ
    key = str(env.get("GOOGLE_VISION_API_KEY") or "").strip()
    if key:
        return GoogleVisionBackend(key, transport=transport)
    return TesseractBackend()
