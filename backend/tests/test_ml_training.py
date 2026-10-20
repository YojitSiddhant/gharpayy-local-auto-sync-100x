import json
from pathlib import Path

import pytest

from backend.app.ml.classification.row_classifier import load_row_classifier
from backend.app.ml.features.ocr_features import FEATURE_NAMES, extract_row_features, feature_schema_fingerprint
from backend.training.scripts.synthetic_row_dataset import synthetic_candidates
from backend.training.scripts.train_row_classifier import train


def test_training_is_deterministic_and_grouped(tmp_path):
    first = train(tmp_path / "one", seed=19, group_count=12)
    second = train(tmp_path / "two", seed=19, group_count=12)
    assert first["model_type"] == second["model_type"]
    assert first["record_counts"] == {"train": 14, "validation": 6, "holdout": 4}
    assert first["results"] == second["results"]
    assert first["feature_schema_fingerprint"] == feature_schema_fingerprint()
    assert first["class_distribution"]["train"] == {"0": 7, "1": 7}


def test_inference_validates_features_checksum_and_probability(tmp_path):
    output = tmp_path / "artifact"
    train(output, seed=7, group_count=12)
    classifier = load_row_classifier(output)
    candidate = synthetic_candidates(12)[0]
    vector = extract_row_features(row=candidate.row, lines=[], width=candidate.width, height=candidate.height)
    result = classifier.predict(vector)
    assert result["classification"] in {0, 1, "abstain"}
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["feature_schema_fingerprint"] == feature_schema_fingerprint()

    metadata = json.loads((output / "metadata.json").read_text())
    metadata["feature_schema_fingerprint"] = "bad"
    (output / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="incompatible"):
        load_row_classifier(output)


def test_missing_and_tampered_artifacts_fail_safely(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_row_classifier(tmp_path / "missing")
    output = tmp_path / "artifact"
    train(output, seed=7, group_count=12)
    model = output / "model.joblib"
    model.write_bytes(model.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        load_row_classifier(output)


def test_incompatible_vector_and_abstention_are_supported(tmp_path):
    output = tmp_path / "artifact"
    train(output, seed=7, group_count=12)
    classifier = load_row_classifier(output, abstain_threshold=1.1)
    candidate = synthetic_candidates(12)[0]
    vector = extract_row_features(row=candidate.row, lines=[], width=candidate.width, height=candidate.height)
    assert classifier.predict(vector)["classification"] == "abstain"
    bad = type(vector)(vector.schema_version, tuple(reversed(FEATURE_NAMES)), vector.values, vector.fingerprint)
    with pytest.raises(ValueError, match="incompatible"):
        classifier.predict(bad)

