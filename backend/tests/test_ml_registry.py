import json
from pathlib import Path

import pytest

from backend.training.scripts.model_registry import eligibility, register_model, rollback, select_shadow, validate_model
from backend.training.scripts.train_row_classifier import train


def evaluation(**overrides):
    value = {"synthetic_only": False, "independent_holdout": True, "approved_dataset": True, "association_regression": False, "metrics": {"phone_precision": .99, "wrong_association_rate": .01, "abstention_rate": .1, "latency_ms": 10, "memory_mb": 10}}
    value.update(overrides); return value


def artifact(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "models" / "one"; target.parent.mkdir(parents=True); train(target, seed=7, group_count=12)
    return target, tmp_path / "models"


def test_eligibility_fails_closed_for_synthetic_and_missing_metrics():
    ok, reasons, _ = eligibility({"synthetic_only": True})
    assert not ok and "synthetic-only evaluation" in reasons and any("missing metric" in r for r in reasons)


def test_registration_validation_and_duplicate_rejection(tmp_path):
    model, root = artifact(tmp_path); report = tmp_path / "eval.json"; report.write_text(json.dumps(evaluation()))
    registry = tmp_path / "registry.json"
    register_model(registry, model, root, model_id="v1", algorithm="logistic", dataset_version="approved-v1", evaluation_path=report, seed=7)
    assert validate_model(registry, "v1", root)["valid"]
    with pytest.raises(ValueError, match="duplicate"):
        register_model(registry, model, root, model_id="v1", algorithm="logistic", dataset_version="approved-v1", evaluation_path=report, seed=7)


def test_untrusted_checksum_and_missing_artifacts_fail(tmp_path):
    model, root = artifact(tmp_path); report = tmp_path / "eval.json"; report.write_text(json.dumps(evaluation())); registry = tmp_path / "registry.json"
    with pytest.raises(ValueError, match="outside trusted"):
        register_model(registry, model, tmp_path / "other", model_id="v1", algorithm="x", dataset_version="x", evaluation_path=report, seed=1)
    model.joinpath("model.joblib").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        register_model(registry, model, root, model_id="v1", algorithm="x", dataset_version="x", evaluation_path=report, seed=1)


def test_selection_and_rollback_are_audited(tmp_path):
    model, root = artifact(tmp_path); report = tmp_path / "eval.json"; report.write_text(json.dumps(evaluation())); registry = tmp_path / "registry.json"
    register_model(registry, model, root, model_id="v1", algorithm="x", dataset_version="x", evaluation_path=report, seed=1)
    model2 = tmp_path / "models" / "two"; model2.mkdir(); train(model2, seed=8, group_count=12); register_model(registry, model2, root, model_id="v2", algorithm="x", dataset_version="x", evaluation_path=report, seed=1)
    select_shadow(registry, "v1", root, "operator"); select_shadow(registry, "v2", root, "operator")
    rollback(registry, "v1", root, "operator", "verified rollback")
    payload = json.loads(registry.read_text()); assert payload["selection"]["shadow_model_id"] == "v1" and payload["audit"][-1]["action"] == "rollback"
