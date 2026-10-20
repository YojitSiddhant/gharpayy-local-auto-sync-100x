"""Trusted offline inference for the Phase 7D row-candidate experiment."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import joblib

from backend.app.ml.features.ocr_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, FeatureVector, feature_schema_fingerprint

ARTIFACT_VERSION = "phase7d-row-classifier-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_vector(vector: FeatureVector) -> list[float]:
    if not isinstance(vector, FeatureVector):
        raise TypeError("expected a Phase 7C FeatureVector")
    if vector.schema_version != FEATURE_SCHEMA_VERSION or vector.fingerprint != feature_schema_fingerprint() or vector.names != FEATURE_NAMES:
        raise ValueError("incompatible feature schema or fingerprint")
    if len(vector.values) != len(FEATURE_NAMES) or not all(math.isfinite(value) for value in vector.values):
        raise ValueError("feature vector must contain finite values")
    return list(vector.values)


class RowClassifier:
    def __init__(self, model: Any, metadata: dict[str, Any], *, abstain_threshold: float = 0.65) -> None:
        self.model = model
        self.metadata = metadata
        self.abstain_threshold = abstain_threshold

    def predict(self, vector: FeatureVector) -> dict[str, Any]:
        values = [_validate_vector(vector)]
        probabilities = self.model.predict_proba(values)[0]
        classes = list(self.model.classes_)
        best_index = max(range(len(probabilities)), key=lambda index: float(probabilities[index]))
        confidence = float(probabilities[best_index])
        label = int(classes[best_index])
        abstained = confidence < self.abstain_threshold
        return {"classification": "abstain" if abstained else label, "confidence": confidence, "abstained": abstained, "model_version": self.metadata["artifact_version"], "feature_schema_fingerprint": vector.fingerprint}


def load_row_classifier(artifact_dir: Path, *, trusted_root: Path | None = None, abstain_threshold: float = 0.65) -> RowClassifier:
    """Load only a generated artifact with a matching local checksum/metadata."""
    artifact_dir = artifact_dir.resolve()
    if trusted_root is not None:
        root = trusted_root.resolve()
        if root not in artifact_dir.parents and artifact_dir != root:
            raise ValueError("model artifact is outside the trusted root")
    model_path, metadata_path = artifact_dir / "model.joblib", artifact_dir / "metadata.json"
    if not model_path.is_file() or not metadata_path.is_file():
        raise FileNotFoundError("model artifact or metadata is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("artifact_version") != ARTIFACT_VERSION or metadata.get("feature_schema_fingerprint") != feature_schema_fingerprint() or metadata.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError("model metadata is incompatible with the current feature schema")
    if metadata.get("model_sha256") != _sha256(model_path):
        raise ValueError("model artifact checksum mismatch")
    try:
        model = joblib.load(model_path)
    except Exception as exc:  # noqa: BLE001 - invalid artifact is a safe load failure
        raise ValueError("model artifact could not be loaded") from exc
    if not hasattr(model, "predict_proba") or not hasattr(model, "classes_"):
        raise ValueError("model artifact does not implement probabilistic classification")
    return RowClassifier(model, metadata, abstain_threshold=abstain_threshold)

