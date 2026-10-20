import json
from pathlib import Path

import pytest

from backend.benchmarks.ocr_baseline import load_manifest, run_benchmark, synthetic_image
from backend.training.scripts.build_manifest import build_manifest, read_labels


ROOT = Path(__file__).parents[1]
LABELS = ROOT / "training" / "datasets" / "synthetic"


def manifest(tmp_path: Path) -> Path:
    payload = build_manifest(read_labels(LABELS), seed=7)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_synthetic_generator_is_deterministic_and_benchmark_is_reproducible(tmp_path):
    labels = read_labels(LABELS)
    assert synthetic_image(labels[0]).tobytes() == synthetic_image(labels[0]).tobytes()
    first = run_benchmark(load_manifest(manifest(tmp_path)), LABELS, split="test")
    second = run_benchmark(load_manifest(manifest(tmp_path)), LABELS, split="test")
    assert first["dataset"] == second["dataset"]
    assert first["metrics"] == second["metrics"]
    assert first["errors"] == second["errors"]


def test_empty_split_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="split is empty"):
        run_benchmark(load_manifest(manifest(tmp_path)), LABELS, split="validation")


def test_invalid_manifest_version_is_rejected(tmp_path):
    path = manifest(tmp_path)
    payload = json.loads(path.read_text())
    payload["schema_version"] = "future-schema"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="incompatible"):
        load_manifest(path)


def test_non_synthetic_label_without_image_is_rejected(tmp_path):
    labels = read_labels(LABELS)
    record = dict(labels[0], example_id="real-without-image", synthetic=False)
    record["screenshot_sha256"] = "f" * 64
    with pytest.raises(FileNotFoundError):
        from backend.benchmarks.ocr_baseline import _image_for
        _image_for(record, tmp_path)

