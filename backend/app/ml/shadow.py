"""Fail-closed, in-process shadow execution for the experimental row classifier."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any

from backend.app.core.config import Settings
from backend.app.ml.classification.row_classifier import RowClassifier, load_row_classifier
from backend.app.ml.features.ocr_features import FeatureVector, extract_row_features


@dataclass(frozen=True)
class ShadowPrediction:
    classification: int | str
    confidence: float
    abstained: bool
    duration_ms: float


@dataclass(frozen=True)
class ShadowRun:
    status: str
    predictions: tuple[ShadowPrediction, ...] = ()
    duration_ms: float = 0.0


@lru_cache(maxsize=8)
def _cached_classifier(artifact_dir: str, trusted_root: str, threshold: float) -> RowClassifier:
    return load_row_classifier(Path(artifact_dir), trusted_root=Path(trusted_root), abstain_threshold=threshold)


def clear_shadow_classifier_cache() -> None:
    """Test/support hook; a backend process normally retains its loaded model."""
    _cached_classifier.cache_clear()


def _classifier(settings: Settings) -> tuple[RowClassifier | None, str]:
    try:
        return _cached_classifier(
            str(Path(settings.ml_artifact_dir).resolve()),
            str(Path(settings.ml_trusted_artifact_root).resolve()),
            settings.ml_abstain_threshold,
        ), "ready"
    except Exception:  # noqa: BLE001 - model failures must never affect OCR
        return None, "unavailable"


def run_shadow_prediction(output: Any, parsed: Any, width: int, height: int, settings: Settings) -> ShadowRun:
    """Run predictions only for comparison; never mutates ``output`` or ``parsed``."""
    if settings.ml_mode != "shadow":
        return ShadowRun("disabled")
    started = monotonic()
    classifier, status = _classifier(settings)
    if classifier is None:
        return ShadowRun(status, duration_ms=(monotonic() - started) * 1000)

    predictions: list[ShadowPrediction] = []
    try:
        for row in parsed.rows:
            vector: FeatureVector = extract_row_features(row=row, lines=output.lines, width=width, height=height)
            item_started = monotonic()
            prediction = classifier.predict(vector)
            item_duration = (monotonic() - item_started) * 1000
            if monotonic() - started > max(0.001, settings.ml_inference_timeout_seconds):
                return ShadowRun("timeout", tuple(predictions), (monotonic() - started) * 1000)
            predictions.append(ShadowPrediction(
                prediction["classification"], float(prediction["confidence"]),
                bool(prediction["abstained"]), item_duration,
            ))
    except Exception:  # noqa: BLE001 - shadow inference is strictly best effort
        return ShadowRun("error", tuple(predictions), (monotonic() - started) * 1000)
    return ShadowRun("ok", tuple(predictions), (monotonic() - started) * 1000)
