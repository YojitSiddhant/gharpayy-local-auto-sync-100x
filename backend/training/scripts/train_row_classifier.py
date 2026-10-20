"""Train and evaluate the first experimental local row classifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from backend.app.ml.features.ocr_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, extract_row_features, feature_schema_fingerprint
from backend.training.scripts.synthetic_row_dataset import SyntheticCandidate, synthetic_candidates

TRAINING_SEED = 20261020


def _split(records: list[SyntheticCandidate], seed: int) -> dict[str, list[SyntheticCandidate]]:
    groups = sorted({record.group_id for record in records})
    random.Random(seed).shuffle(groups)
    n = len(groups); train_end = max(1, round(n * 0.6)); validation_end = max(train_end + 1, round(n * 0.8))
    assignment = {group: "train" if i < train_end else "validation" if i < validation_end else "holdout" for i, group in enumerate(groups)}
    return {split: [record for record in records if assignment[record.group_id] == split] for split in ("train", "validation", "holdout")}


def _matrix(records: list[SyntheticCandidate]) -> tuple[np.ndarray, np.ndarray]:
    vectors = [extract_row_features(row=record.row, lines=[], width=record.width, height=record.height) for record in records]
    return np.asarray([vector.values for vector in vectors], dtype=np.float64), np.asarray([record.label for record in records], dtype=np.int64)


def _metrics(model: Any, records: list[SyntheticCandidate], *, threshold: float = 0.65) -> dict[str, Any]:
    matrix, truth = _matrix(records)
    probabilities = model.predict_proba(matrix)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    abstained = (np.maximum(probabilities, 1 - probabilities) < threshold)
    cm = confusion_matrix(truth, predictions, labels=[0, 1]).tolist()
    return {
        "records": len(records), "precision": float(precision_score(truth, predictions, zero_division=0)),
        "recall": float(recall_score(truth, predictions, zero_division=0)), "f1": float(f1_score(truth, predictions, zero_division=0)),
        "false_positive_rate": float(cm[0][1] / max(1, cm[0][0] + cm[0][1])), "confusion_matrix_labels_0_1": cm,
        "abstention_rate": float(np.mean(abstained)) if len(abstained) else 0.0,
        "mean_confidence": float(np.mean(np.maximum(probabilities, 1 - probabilities))) if len(probabilities) else 0.0,
    }


def _baseline(records: list[SyntheticCandidate]) -> dict[str, Any]:
    truth = np.asarray([record.label for record in records])
    predictions = np.asarray([int(record.row.phone_status == "candidate" or record.row.visible_timestamp_text is not None) for record in records])
    cm = confusion_matrix(truth, predictions, labels=[0, 1]).tolist()
    return {"precision": float(precision_score(truth, predictions, zero_division=0)), "recall": float(recall_score(truth, predictions, zero_division=0)), "f1": float(f1_score(truth, predictions, zero_division=0)), "false_positive_rate": float(cm[0][1] / max(1, cm[0][0] + cm[0][1])), "confusion_matrix_labels_0_1": cm}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def train(output_dir: Path, *, seed: int = TRAINING_SEED, group_count: int = 30) -> dict[str, Any]:
    records = synthetic_candidates(group_count)
    splits = _split(records, seed)
    if len({record.group_id for record in splits["train"]} & {record.group_id for record in splits["holdout"]}):
        raise ValueError("group leakage detected")
    train_x, train_y = _matrix(splits["train"])
    models = {
        "logistic_regression": LogisticRegression(random_state=seed, max_iter=1000, class_weight="balanced"),
        "random_forest": RandomForestClassifier(n_estimators=80, random_state=seed, class_weight="balanced", n_jobs=1),
    }
    results = {}
    for name, model in models.items():
        model.fit(train_x, train_y)
        results[name] = {"validation": _metrics(model, splits["validation"])}
    chosen_name = max(results, key=lambda name: (results[name]["validation"]["f1"], name == "logistic_regression"))
    chosen = models[chosen_name]
    results[chosen_name]["holdout"] = _metrics(chosen, splits["holdout"])
    results["deterministic_baseline"] = {"holdout": _baseline(splits["holdout"])}
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.joblib"
    metadata_path = output_dir / "metadata.json"
    joblib.dump(chosen, model_path)
    metadata = {
        "artifact_version": "phase7d-row-classifier-v1", "model_type": chosen_name, "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_fingerprint": feature_schema_fingerprint(), "feature_names": list(FEATURE_NAMES), "dataset_version": "synthetic-row-candidates-v1",
        "training_seed": seed, "group_count": group_count, "record_counts": {key: len(value) for key, value in splits.items()},
        "class_distribution": {key: {str(label): sum(record.label == label for record in value) for label in (0, 1)} for key, value in splits.items()},
        "selected_model_validation": results[chosen_name]["validation"], "results": results, "abstention_threshold": 0.65,
        "model_sha256": _sha256(model_path), "generated_at": "deterministic-experiment",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("backend/model_artifacts/phase7d-row-classifier"))
    parser.add_argument("--groups", type=int, default=30)
    args = parser.parse_args()
    metadata = train(args.output_dir, group_count=args.groups)
    print(json.dumps({"model_type": metadata["model_type"], "record_counts": metadata["record_counts"], "holdout": metadata["results"][metadata["model_type"]]["holdout"], "artifact": str(args.output_dir / "model.joblib")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

