import shutil

import pytesseract
import pytest
from PIL import Image

from backend.app.ocr import engine as engine_module
from backend.app.ocr.engine import OcrError, OcrTimeoutError, OcrUnavailableError, TesseractEngine
from backend.app.ocr.image_validation import decode_rgb
from backend.app.ocr.whatsapp_parser import parse_chat_list
from backend.tests.conftest import chat_list_image

HAS_TESSERACT = shutil.which("tesseract") is not None


@pytest.fixture
def stub_engine_ready(monkeypatch):
    monkeypatch.setattr(engine_module, "tesseract_version", lambda: "5.5.3")
    monkeypatch.setattr(engine_module, "ensure_language_available", lambda: None)


def _raise(exc):
    def runner(*args, **kwargs):
        raise exc
    return runner


def test_tesseract_timeout_is_reported(monkeypatch, stub_engine_ready):
    monkeypatch.setattr(pytesseract, "image_to_data", _raise(RuntimeError("Tesseract process timeout")))
    with pytest.raises(OcrTimeoutError):
        TesseractEngine(1, 11).run(Image.new("RGB", (100, 100), "white"))


def test_tesseract_failure_is_reported(monkeypatch, stub_engine_ready):
    monkeypatch.setattr(pytesseract, "image_to_data", _raise(pytesseract.TesseractError(1, "boom")))
    with pytest.raises(OcrError) as exc:
        TesseractEngine(1, 11).run(Image.new("RGB", (100, 100), "white"))
    assert exc.value.code == "OCR_FAILED"


def test_missing_language_data_is_unavailable(monkeypatch, stub_engine_ready):
    monkeypatch.setattr(pytesseract, "image_to_data", _raise(pytesseract.TesseractError(1, "Failed loading language 'eng'")))
    with pytest.raises(OcrUnavailableError):
        TesseractEngine(1, 11).run(Image.new("RGB", (100, 100), "white"))


def test_missing_executable_is_unavailable(monkeypatch):
    engine_module.tesseract_version.cache_clear()
    monkeypatch.setattr(pytesseract, "get_tesseract_version", _raise(pytesseract.TesseractNotFoundError()))
    try:
        with pytest.raises(OcrUnavailableError):
            TesseractEngine(1, 11).run(Image.new("RGB", (100, 100), "white"))
    finally:
        engine_module.tesseract_version.cache_clear()


def test_small_images_are_upscaled_and_coordinates_mapped_back(monkeypatch, stub_engine_ready):
    captured = {}

    def fake_data(image, **kwargs):
        captured["size"] = image.size
        return {"text": ["Hello"], "conf": ["90"], "left": [100], "top": [40], "width": [60], "height": [20],
                "block_num": [1], "par_num": [1], "line_num": [1]}

    monkeypatch.setattr(pytesseract, "image_to_data", fake_data)
    output = TesseractEngine(5, 11).run(Image.new("RGB", (300, 500), "white"))
    assert captured["size"] == (600, 1000)
    assert output.processing_scale == 2.0
    assert (output.lines[0].left, output.lines[0].top, output.lines[0].right) == (50, 20, 80)


@pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract executable not installed")
def test_real_english_tesseract_on_synthetic_chat_list():
    """Real local OCR (no mocks) on a synthetic, privacy-safe chat-list image."""
    image = decode_rgb(chat_list_image())
    output = TesseractEngine(30, 11).run(image)
    assert output.language == "eng" and output.word_count > 10
    assert "private room" in output.raw_text
    rows = parse_chat_list(output.lines, image.width, image.height).rows
    by_phone = {r.phone_normalized: r for r in rows if r.phone_normalized}
    assert "+919876543210" in by_phone
    assert by_phone["+919876543210"].visible_timestamp_text == "2:52 pm"
    # The number inside Akhil's message must never become an identity.
    assert "+919449190529" not in by_phone
    # A partial (nine-digit) header must stay unverified and quarantined, never padded or normalized.
    partial = [r for r in rows if r.phone_raw and r.phone_raw.replace(" ", "") == "+91987654321"]
    assert partial and partial[0].phone_status == "ambiguous" and partial[0].quarantined
