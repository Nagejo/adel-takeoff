"""OCR backend selection. Vision is mocked. Tests do not call the network."""

import fitz
import pytest
from PIL import Image

from adel_takeoff.extract import Word, is_low_ocr
from adel_takeoff.ocr import (
    GoogleVisionBackend,
    OcrToken,
    select_backend,
    tokens_from_vision,
)
from adel_takeoff.text_index import _recognize


def test_tesseract_is_the_default_until_the_key_is_set(monkeypatch):
    monkeypatch.delenv("GOOGLE_VISION_API_KEY", raising=False)
    assert select_backend().name == "tesseract"
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "   ")
    assert select_backend().name == "tesseract"
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "test-key")
    backend = select_backend()
    assert isinstance(backend, GoogleVisionBackend)
    assert backend.name == "google_vision"
    assert backend.api_key == "test-key"


def test_vision_is_mocked_and_uses_the_same_confidence(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("live Vision call")

    monkeypatch.setattr("httpx.post", refuse)

    def transport(url, payload, timeout):
        assert "key=test-key" in url
        assert timeout == 30
        assert payload["requests"][0]["features"][0]["type"] == "DOCUMENT_TEXT_DETECTION"
        return {
            "responses": [
                {
                    "fullTextAnnotation": {
                        "pages": [
                            {
                                "blocks": [
                                    {
                                        "paragraphs": [
                                            {
                                                "words": [
                                                    {
                                                        "symbols": [
                                                            {"text": "L"},
                                                            {"text": "P"},
                                                            {"text": "1"},
                                                        ],
                                                        "confidence": 0.42,
                                                        "boundingBox": {
                                                            "vertices": [
                                                                {"x": 10, "y": 20},
                                                                {"x": 40, "y": 20},
                                                                {"x": 40, "y": 32},
                                                                {"x": 10, "y": 32},
                                                            ]
                                                        },
                                                    },
                                                    {
                                                        "symbols": [{"text": "A", "confidence": 0.91}],
                                                        "boundingBox": {
                                                            "vertices": [
                                                                {"x": 1, "y": 2},
                                                                {"x": 9, "y": 2},
                                                                {"x": 9, "y": 14},
                                                                {"x": 1, "y": 14},
                                                            ]
                                                        },
                                                    },
                                                    {
                                                        "symbols": [{"text": "Z"}],
                                                        "boundingBox": {
                                                            "vertices": [
                                                                {"x": 0, "y": 0},
                                                                {"x": 4, "y": 4},
                                                            ]
                                                        },
                                                    },
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }

    image = Image.new("RGB", (32, 32), "white")
    tokens = GoogleVisionBackend("test-key", transport=transport).recognize(image)
    assert [token.text for token in tokens] == ["LP1", "A", "Z"]
    assert tokens[0].confidence == 0.42
    assert tokens[1].confidence == 0.91
    assert tokens[2].confidence is None
    low = Word(0, 0, 1, 1, "LP1", source="ocr", confidence=tokens[0].confidence)
    high = Word(0, 0, 1, 1, "A", source="ocr", confidence=tokens[1].confidence)
    missing = Word(0, 0, 1, 1, "Z", source="ocr", confidence=tokens[2].confidence)
    assert is_low_ocr(low)
    assert not is_low_ocr(high)
    assert is_low_ocr(missing)
    assert tokens_from_vision({"responses": []}) == []


def test_page_ocr_uses_the_selected_backend(monkeypatch):
    monkeypatch.setenv("GOOGLE_VISION_API_KEY", "test-key")
    seen = []

    class Fake:
        name = "google_vision"

        def recognize(self, image):
            seen.append(image.size)
            return [OcrToken("LP1", 12, 18, 30, 14, 0.42)]

    monkeypatch.setattr(
        "adel_takeoff.ocr.select_backend",
        lambda environ=None, transport=None: Fake(),
    )
    document = fitz.open()
    page = document.new_page(width=120, height=80)
    words = _recognize(page)
    document.close()
    assert seen
    assert len(words) == 1
    assert words[0].text == "LP1"
    assert words[0].source == "ocr"
    assert words[0].confidence == pytest.approx(0.42)
    assert is_low_ocr(words[0])
