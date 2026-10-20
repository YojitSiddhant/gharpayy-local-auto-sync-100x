import json
from pathlib import Path

import pytest

from backend.benchmarks.ocr_baseline import load_manifest
from backend.benchmarks.ocr_ml_evaluation import evaluate
from backend.training.scripts.build_manifest import build_manifest, read_labels
from backend.training.scripts.train_row_classifier import train


FIXTURE = Path("backend/training/datasets/synthetic")


def test_synthetic_comparison_is_reproducible_and_explicit(tmp_path):
    manifest_path = tmp_path / "manifest.json"; manifest_path.write_text(json.dumps(build_manifest(read_labels(FIXTURE), seed=7)))
    artifact = tmp_path / "artifact"; train(artifact, seed=7, group_count=12)
    first = evaluate(load_manifest(manifest_path), FIXTURE, artifact, tmp_path, split="test")
    second = evaluate(load_manifest(manifest_path), FIXTURE, artifact, tmp_path, split="test")
    assert first["baseline"]["metrics"] == second["baseline"]["metrics"]
    assert first["ml_shadow"]["metrics"]["row_detection"] == second["ml_shadow"]["metrics"]["row_detection"]


def test_real_evaluation_requires_explicit_approval(tmp_path):
    records = read_labels(FIXTURE)
    record = dict(records[0]); record["synthetic"] = False; record["review"] = dict(record["review"]); record["review"].pop("approved_for_evaluation", None)
    labels = tmp_path / "labels"; labels.mkdir(); (labels / "labels.json").write_text(json.dumps([record]))
    manifest = build_manifest([record]); artifact = tmp_path / "artifact"; train(artifact, seed=7, group_count=12)
    with pytest.raises(ValueError, match="not explicitly approved"):
        evaluate(manifest, labels, artifact, tmp_path, split="train")
