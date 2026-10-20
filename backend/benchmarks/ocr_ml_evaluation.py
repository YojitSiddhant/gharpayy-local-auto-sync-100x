"""Compare unchanged OCR/parser output with the Phase 7D row classifier offline."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.ml.classification.row_classifier import load_row_classifier
from backend.app.ml.evaluation.metrics import character_error_rate, exact_match_accuracy, row_detection_metrics, timestamp_exact_match_accuracy, word_error_rate
from backend.app.ml.features.ocr_features import extract_row_features
from backend.app.ocr.engine import TesseractEngine, tesseract_version
from backend.app.ocr.whatsapp_parser import PARSER_VERSION, parse_chat_list
from backend.benchmarks.ocr_baseline import _field, _image_for, _latency, _load_labels, _truth, load_manifest, run_benchmark


def _approved_for_evaluation(record: dict[str, Any]) -> bool:
    return bool(record.get("synthetic") or record.get("review", {}).get("approved_for_evaluation") is True)


def evaluate(manifest: dict[str, Any], labels_dir: Path, artifact_dir: Path, trusted_root: Path, *, split: str, settings: Settings | None = None) -> dict[str, Any]:
    entries = [entry for entry in manifest["entries"] if entry.get("split") == split]
    if not entries: raise ValueError(f"manifest split is empty: {split}")
    labels = _load_labels(labels_dir)
    for entry in entries:
        record = labels.get(entry["example_id"])
        if record is None or not _approved_for_evaluation(record): raise ValueError("evaluation example is not explicitly approved")
    settings = settings or Settings(_env_file=None)
    classifier = load_row_classifier(artifact_dir, trusted_root=trusted_root)
    baseline = run_benchmark(manifest, labels_dir, split=split, settings=settings)
    engine = TesseractEngine(settings.ocr_tesseract_timeout_seconds, settings.ocr_tesseract_psm)
    accepted: list[int] = []; truth: list[int] = []; shadow_latency: list[float] = []; total_latency: list[float] = []
    fields = {name: {"pred": [], "truth": [], "status": []} for name in ("phone", "message", "timestamp")}
    abstained = 0; errors: dict[str, int] = {}
    for entry in entries:
        record = labels[entry["example_id"]]; image, _ = _image_for(record, labels_dir)
        started = time.perf_counter(); output = engine.run(image); parsed = parse_chat_list(output.lines, image.width, image.height)
        row = parsed.rows[0] if parsed.rows else None
        truth.append(1); predictions = []
        shadow_started = time.perf_counter()
        for candidate in parsed.rows:
            vector = extract_row_features(row=candidate, lines=output.lines, width=image.width, height=image.height)
            predictions.append(classifier.predict(vector))
        shadow_latency.append((time.perf_counter() - shadow_started) * 1000); total_latency.append((time.perf_counter() - started) * 1000)
        positives = [item for item in predictions if item["classification"] == 1]
        if any(item["abstained"] for item in predictions): abstained += 1
        accepted.append(1 if positives else 0)
        if len(positives) > 1: errors["false_positive_row"] = errors.get("false_positive_row", 0) + len(positives) - 1
        selected = positives[0] if positives else None
        # The classifier does not alter parser fields; only accepted-row accounting changes.
        selected_row = row if selected is not None else None
        for name in fields:
            expected, status = _truth(record, name); fields[name]["truth"].append(expected); fields[name]["status"].append(status); fields[name]["pred"].append(_field(selected_row, name) if selected_row else None)
    ml_fields = {
        "phone_exact_match_accuracy": exact_match_accuracy(fields["phone"]["pred"], fields["phone"]["truth"], statuses=fields["phone"]["status"]),
        "message_cer": character_error_rate(fields["message"]["pred"], fields["message"]["truth"], statuses=fields["message"]["status"]),
        "message_wer": word_error_rate(fields["message"]["pred"], fields["message"]["truth"], statuses=fields["message"]["status"]),
        "timestamp_exact_match_accuracy": timestamp_exact_match_accuracy(fields["timestamp"]["pred"], fields["timestamp"]["truth"], statuses=fields["timestamp"]["status"]),
    }
    return {"evaluation_version": "phase7h-ocr-ml-v1", "dataset": baseline["dataset"], "provenance": {"tesseract": tesseract_version(), "parser": PARSER_VERSION, "model_artifact": str(artifact_dir.resolve()), "feature_schema": classifier.metadata["feature_schema_fingerprint"]}, "baseline": {"metrics": baseline["metrics"], "latency": baseline["latency"]}, "ml_shadow": {"metrics": {"row_detection": row_detection_metrics(accepted, truth), "false_positive_row_rate": sum(1 for value in accepted if value == 0) / len(accepted), "missed_row_rate": sum(1 for value in accepted if value == 0) / len(accepted), "abstention_rate": abstained / len(entries), **ml_fields}, "latency": _latency(shadow_latency), "total_ocr_latency": _latency(total_latency), "errors": errors}, "limitations": ["The Phase 7D classifier classifies row candidates only; it does not improve OCR characters or parser normalization.", "Synthetic results are not real-world accuracy.", "No customer evidence was discovered or processed."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--labels", type=Path, required=True); parser.add_argument("--artifact", type=Path, required=True); parser.add_argument("--trusted-root", type=Path, required=True); parser.add_argument("--split", choices=("train", "validation", "test"), default="test"); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); report = evaluate(load_manifest(args.manifest), args.labels, args.artifact, args.trusted_root, split=args.split); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n"); print(json.dumps({"status": "evaluated", "split": args.split, "screenshots": report["dataset"]["screenshots"]}, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
