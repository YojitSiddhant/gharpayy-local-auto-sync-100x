from PIL import Image

from backend.app.ocr.paddle_engine import PaddleOCREngine, PaddleUnavailableError


def test_missing_paddle_dependency_is_graceful(monkeypatch):
    engine = PaddleOCREngine(python_path="/missing/python")
    try: engine.run(Image.new("RGB", (20, 20), "white"))
    except PaddleUnavailableError as exc: assert "interpreter" in str(exc)
    else: raise AssertionError("expected optional dependency failure")


def test_paddle_result_adapter(monkeypatch):
    engine = PaddleOCREngine()
    monkeypatch.setattr(engine, "_request", lambda image: {"ok": True, "result": {
        "rec_texts": ["+91 98765 43210"], "rec_scores": [0.91], "rec_boxes": [[1, 2, 100, 20]]}})
    output = engine.run(Image.new("RGB", (120, 30), "white"))
    assert output.raw_text == "+91 98765 43210"
    assert output.lines[0].words[0].confidence == 91.0
