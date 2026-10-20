from types import SimpleNamespace

from backend.app.core.config import Settings
from backend.app.ml import shadow


def test_shadow_is_off_by_default_and_does_not_load(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.ml_mode == "off"
    monkeypatch.setattr(shadow, "_classifier", lambda _: (_ for _ in ()).throw(AssertionError("loaded")))
    result = shadow.run_shadow_prediction(SimpleNamespace(lines=[]), SimpleNamespace(rows=[]), 100, 100, settings)
    assert result.status == "disabled"


def test_shadow_prediction_is_private_and_does_not_change_inputs(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, ml_mode="shadow", ml_artifact_dir=str(tmp_path), ml_trusted_artifact_root=str(tmp_path))
    classifier = SimpleNamespace(predict=lambda vector: {
        "classification": 1, "confidence": 0.91, "abstained": False,
    })
    monkeypatch.setattr(shadow, "_classifier", lambda _: (classifier, "ready"))
    monkeypatch.setattr(shadow, "extract_row_features", lambda **kwargs: object())
    row = SimpleNamespace(display_header_text="+91 9876543210", message_preview="private message")
    output = SimpleNamespace(lines=[])
    parsed = SimpleNamespace(rows=[row])
    result = shadow.run_shadow_prediction(output, parsed, 100, 100, settings)
    assert result.status == "ok"
    assert result.predictions[0].classification == 1
    assert "9876543210" not in repr(result)
    assert "private message" not in repr(result)
    assert parsed.rows[0] is row and output.lines == []


def test_shadow_failures_are_non_fatal(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, ml_mode="shadow", ml_artifact_dir=str(tmp_path), ml_trusted_artifact_root=str(tmp_path))
    monkeypatch.setattr(shadow, "_classifier", lambda _: (None, "unavailable"))
    result = shadow.run_shadow_prediction(SimpleNamespace(lines=[]), SimpleNamespace(rows=[]), 100, 100, settings)
    assert result.status == "unavailable"


def test_shadow_prediction_error_is_reported_without_raising(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, ml_mode="shadow", ml_artifact_dir=str(tmp_path), ml_trusted_artifact_root=str(tmp_path))
    classifier = SimpleNamespace(predict=lambda vector: (_ for _ in ()).throw(RuntimeError("bad model")))
    monkeypatch.setattr(shadow, "_classifier", lambda _: (classifier, "ready"))
    monkeypatch.setattr(shadow, "extract_row_features", lambda **kwargs: object())
    result = shadow.run_shadow_prediction(SimpleNamespace(lines=[]), SimpleNamespace(rows=[object()]), 100, 100, settings)
    assert result.status == "error"
