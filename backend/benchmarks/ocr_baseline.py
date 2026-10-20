"""Benchmark the existing local Tesseract and WhatsApp parser.

This command never discovers database evidence. It requires an explicit
manifest and labels directory. Synthetic labels without image files are
rendered deterministically; real labels require an explicit ``image_path``.

Example:
  backend/.venv/bin/python -m backend.benchmarks.ocr_baseline \
    --manifest /tmp/phase7a-manifest.json \
    --labels backend/training/datasets/synthetic \
    --split test --format both
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from backend.app.core.config import Settings
from backend.app.ml.evaluation.metrics import (
    character_error_rate,
    exact_match_accuracy,
    invalid_number_acceptance_rate,
    precision_recall,
    review_rate,
    row_detection_metrics,
    timestamp_exact_match_accuracy,
    word_error_rate,
    wrong_assignment_rate,
)
from backend.app.ocr.engine import TesseractEngine, tesseract_version
from backend.app.ocr.whatsapp_parser import PARSER_VERSION, parse_chat_list
from backend.training.scripts.build_manifest import SCHEMA_VERSION, validate_label

MANIFEST_VERSION = "phase7a-manifest-v1"


def _load_labels(directory: Path) -> dict[str, dict]:
    labels: dict[str, dict] = {}
    for path in sorted(directory.rglob("*.json")):
        if path.name == "schema.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload if isinstance(payload, list) else payload.get("examples") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError(f"{path}: expected a JSON list or examples object")
        for value in values:
            record = validate_label(value, path)
            if record["example_id"] in labels:
                raise ValueError(f"duplicate example_id: {record['example_id']}")
            labels[record["example_id"]] = record
    return labels


def load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != MANIFEST_VERSION or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("incompatible manifest or label schema version")
    if not isinstance(payload.get("entries"), list):
        raise ValueError("manifest entries must be a list")
    return payload


def _font(size: int):
    return ImageFont.load_default(size=size)


def synthetic_image(record: dict) -> Image.Image:
    """Render one privacy-safe label into a deterministic portrait screenshot."""
    width, height = record["image"]["width"], record["image"]["height"]
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    scale = width / 720
    pos = lambda x, y: (round(x * scale), round(y * scale))
    draw.text(pos(40, 30), "Chats", fill="black", font=_font(round(30 * scale)))
    fields = record["fields"]
    phone = fields["phone"].get("visible_text")
    message = fields["message"].get("visible_text")
    timestamp = fields["timestamp"].get("visible_text")
    y = round(170 * scale)
    draw.ellipse((round(30 * scale), y - round(5 * scale), round(110 * scale), y + round(75 * scale)), fill="#cccccc")
    if phone:
        header = phone
    else:
        header = "Unreadable contact" if fields["phone"]["status"] == "unreadable" else ""
    if header:
        draw.text(pos(140, 170), header, fill="black", font=_font(round(30 * scale)))
    if timestamp:
        draw.text(pos(560, 174), timestamp, fill="#555555", font=_font(round(26 * scale)))
    if message:
        draw.text(pos(140, 218), message, fill="#444444", font=_font(round(26 * scale)))
    return image


def _image_for(record: dict, labels_dir: Path) -> tuple[Image.Image, str]:
    image_path = record.get("image_path")
    if image_path:
        path = Path(image_path)
        if not path.is_absolute():
            path = labels_dir / path
        if not path.is_file():
            raise FileNotFoundError("explicit image_path does not exist")
        return Image.open(path).convert("RGB"), "approved-image-file"
    if record.get("synthetic") is True:
        return synthetic_image(record), "deterministic-synthetic-generator"
    raise FileNotFoundError("no image_path supplied for non-synthetic label")


def _field(row: Any, name: str) -> str | None:
    if name == "phone":
        return row.phone_normalized
    if name == "message":
        return row.message_preview
    return row.visible_timestamp_text


def _truth(record: dict, name: str) -> tuple[str | None, str]:
    field = record["fields"][name]
    return field.get("normalized") if name == "phone" else field.get("visible_text"), field["status"]


def _percent(value: float) -> float:
    return round(value * 100, 2)


def _latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50_ms": None, "p95_ms": None, "mean_ms": None}
    values = sorted(values)
    return {"p50_ms": round(statistics.median(values), 2), "p95_ms": round(values[min(len(values) - 1, max(0, round(len(values) * 0.95) - 1))], 2), "mean_ms": round(statistics.fmean(values), 2)}


def run_benchmark(manifest: dict, labels_dir: Path, *, split: str, settings: Settings | None = None) -> dict:
    entries = [entry for entry in manifest["entries"] if entry.get("split") == split]
    if not entries:
        raise ValueError(f"manifest split is empty: {split}")
    labels = _load_labels(labels_dir)
    settings = settings or Settings(_env_file=None)
    engine = TesseractEngine(settings.ocr_tesseract_timeout_seconds, settings.ocr_tesseract_psm)
    phone_pred: list[str | None] = []; phone_truth: list[str | None] = []; phone_status: list[str] = []
    message_pred: list[str | None] = []; message_truth: list[str | None] = []; message_status: list[str] = []
    time_pred: list[str | None] = []; time_truth: list[str | None] = []; time_status: list[str] = []
    predicted_rows: list[int] = []; truth_rows: list[int] = []; associations: list[bool] = []
    review_statuses: list[str] = []; latencies: list[float] = []; errors: dict[str, int] = {}
    groups: set[str] = set(); sources: set[str] = set(); labeled_rows = 0
    for entry in entries:
        record = labels.get(entry.get("example_id"))
        if record is None:
            raise ValueError(f"manifest entry label is missing: {entry.get('example_id')}")
        if record["screenshot_sha256"] != entry.get("screenshot_sha256") or record["capture_group_id"] != entry.get("capture_group_id"):
            raise ValueError("manifest and label metadata disagree")
        if not record["synthetic"] and not record["review"].get("approved_for_training"):
            raise ValueError("non-synthetic benchmark example is not explicitly approved")
        image, source = _image_for(record, labels_dir)
        groups.add(record["capture_group_id"]); sources.add(source); labeled_rows += 1
        started = time.perf_counter()
        output = engine.run(image)
        parsed = parse_chat_list(output.lines, image.width, image.height)
        latencies.append((time.perf_counter() - started) * 1000)
        expected_row = record["row"]["index"]
        # Phase 7A fixtures currently label one target row per screenshot.
        # Evaluate detection per screenshot so row indices from separate images
        # are never compared as if they belonged to one shared image.
        truth_rows.append(1)
        predicted_rows.append(1 if parsed.rows else 0)
        if len(parsed.rows) > 1:
            errors["spurious_rows"] = errors.get("spurious_rows", 0) + len(parsed.rows) - 1
        row = parsed.rows[0] if parsed.rows else None
        if row is None:
            errors["missing_row"] = errors.get("missing_row", 0) + 1
            for name, preds, truths, statuses in (("phone", phone_pred, phone_truth, phone_status), ("message", message_pred, message_truth, message_status), ("timestamp", time_pred, time_truth, time_status)):
                truth, status = _truth(record, name); preds.append(None); truths.append(truth); statuses.append(status)
            review_statuses.append("abstain")
            continue
        associations.append(row.position == expected_row or len(parsed.rows) == 1)
        for name, preds, truths, statuses in (("phone", phone_pred, phone_truth, phone_status), ("message", message_pred, message_truth, message_status), ("timestamp", time_pred, time_truth, time_status)):
            truth, status = _truth(record, name); preds.append(_field(row, name)); truths.append(truth); statuses.append(status)
            if truth is not None and _field(row, name) != truth and status == "verified":
                errors[f"wrong_{name}"] = errors.get(f"wrong_{name}", 0) + 1
            elif truth is None and _field(row, name) is not None:
                errors[f"spurious_{name}"] = errors.get(f"spurious_{name}", 0) + 1
        review_statuses.append("review" if row.quarantined or row.warnings else "accepted")
    phone_pr = precision_recall(phone_pred, phone_truth, valid_truth=lambda value: value is not None and bool(str(value)))
    report = {
        "benchmark_version": "phase7b-baseline-v1",
        "dataset": {"manifest_version": manifest["manifest_version"], "dataset_version": sorted({labels[e["example_id"]]["dataset_version"] for e in entries}), "split": split, "screenshots": len(entries), "labeled_rows": labeled_rows, "capture_groups": len(groups), "image_sources": sorted(sources)},
        "provenance": {"engine": "tesseract", "engine_version": tesseract_version(), "parser_version": PARSER_VERSION, "psm": settings.ocr_tesseract_psm},
        "metrics": {"phone_exact_match_accuracy_percent": _percent(exact_match_accuracy(phone_pred, phone_truth, statuses=phone_status)), "phone_precision_percent": _percent(phone_pr["precision"]), "phone_recall_percent": _percent(phone_pr["recall"]), "invalid_number_acceptance_rate_percent": _percent(invalid_number_acceptance_rate(phone_pred)), "message_character_error_rate": character_error_rate(message_pred, message_truth, statuses=message_status), "message_word_error_rate": word_error_rate(message_pred, message_truth, statuses=message_status), "timestamp_exact_match_accuracy_percent": _percent(timestamp_exact_match_accuracy(time_pred, time_truth, statuses=time_status)), "row_detection": row_detection_metrics(predicted_rows, truth_rows), "field_to_row_association_accuracy_percent": _percent(sum(associations) / len(associations) if associations else 0.0), "wrong_assignment_rate_percent": _percent(wrong_assignment_rate(phone_pred, phone_truth) if phone_truth else 0.0), "review_abstention_rate_percent": _percent(review_rate(review_statuses))},
        "latency": _latency(latencies),
        "errors": errors,
        "limitations": ["Synthetic benchmark results are not real-world accuracy.", "No customer evidence was discovered or processed.", "Only the explicitly selected manifest split was evaluated.", "The existing Tesseract engine and parser were not modified or tuned."],
    }
    return report


def summary(report: dict) -> str:
    dataset = report["dataset"]; metrics = report["metrics"]
    return "\n".join([f"Phase 7B baseline ({dataset['split']} split)", f"Screenshots: {dataset['screenshots']} · labeled rows: {dataset['labeled_rows']} · capture groups: {dataset['capture_groups']}", f"Phone exact match: {metrics['phone_exact_match_accuracy_percent']}% · precision: {metrics['phone_precision_percent']}% · recall: {metrics['phone_recall_percent']}%", f"Message CER: {metrics['message_character_error_rate']} · timestamp exact match: {metrics['timestamp_exact_match_accuracy_percent']}%", f"Row detection: P {metrics['row_detection']['precision']:.3f} · R {metrics['row_detection']['recall']:.3f}", f"Latency: p50 {report['latency']['p50_ms']} ms · p95 {report['latency']['p95_ms']} ms", "Limitations: synthetic/approved fixture scope only; real-world accuracy remains unmeasured."])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "validation", "test"), default="test")
    parser.add_argument("--format", choices=("json", "summary", "both"), default="both")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmark(load_manifest(args.manifest), args.labels, split=args.split)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.format in {"json", "both"}:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.format in {"summary", "both"}:
        print(summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

