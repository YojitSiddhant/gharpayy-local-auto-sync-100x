"""Build a deterministic, leakage-safe Phase 7A dataset manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "phase7a-label-v1"
STATUSES = {"verified", "unreadable", "not_visible", "partial", "ambiguous", "truncated"}


def _fail(path: Path, reason: str) -> ValueError:
    return ValueError(f"{path}: {reason}")


def validate_label(item: Any, path: Path = Path("<memory>")) -> dict:
    if not isinstance(item, dict):
        raise _fail(path, "label must be an object")
    required = {"schema_version", "example_id", "screenshot_sha256", "capture_group_id", "image", "row", "fields", "review", "dataset_version", "synthetic"}
    missing = required - item.keys()
    if missing:
        raise _fail(path, f"missing fields: {', '.join(sorted(missing))}")
    if item["schema_version"] != SCHEMA_VERSION or not isinstance(item["synthetic"], bool):
        raise _fail(path, "invalid schema version or synthetic marker")
    if not isinstance(item["example_id"], str) or not item["example_id"]:
        raise _fail(path, "example_id must be non-empty")
    digest = item["screenshot_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise _fail(path, "screenshot_sha256 must be lowercase SHA-256")
    group = item["capture_group_id"]
    if not isinstance(group, str) or not group:
        raise _fail(path, "capture_group_id must be non-empty")
    image = item["image"]
    if not isinstance(image, dict) or not all(isinstance(image.get(k), int) and image[k] > 0 for k in ("width", "height")):
        raise _fail(path, "image width and height must be positive integers")
    crop = image.get("crop")
    if not isinstance(crop, dict) or not all(isinstance(crop.get(k), int) for k in ("left", "top", "right", "bottom")) or not (0 <= crop["left"] < crop["right"] <= image["width"] and 0 <= crop["top"] < crop["bottom"] <= image["height"]):
        raise _fail(path, "crop must be inside image bounds")
    row = item["row"]
    bbox = row.get("bbox") if isinstance(row, dict) else None
    if not isinstance(row, dict) or not isinstance(row.get("index"), int) or row["index"] < 1 or not isinstance(bbox, dict):
        raise _fail(path, "row index and bbox are required")
    fields = item["fields"]
    if not isinstance(fields, dict) or set(fields) != {"phone", "message", "timestamp"}:
        raise _fail(path, "fields must contain phone, message, and timestamp")
    for name, field in fields.items():
        if not isinstance(field, dict) or field.get("status") not in STATUSES or "visible_text" not in field:
            raise _fail(path, f"invalid {name} field status/value")
        if field["status"] == "verified" and not isinstance(field["visible_text"], str):
            raise _fail(path, f"verified {name} must have visible_text")
    review = item["review"]
    if not isinstance(review, dict) or not isinstance(review.get("approved_for_training"), bool) or not review.get("reviewer") or review.get("status") not in STATUSES:
        raise _fail(path, "review approval, reviewer, and status are required")
    return item


def read_labels(directory: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(directory.rglob("*.json")):
        if path.name == "schema.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload if isinstance(payload, list) else payload.get("examples") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise _fail(path, "expected a JSON list or an object with examples")
        records.extend(validate_label(value, path) for value in values)
    return records


def build_manifest(records: list[dict], *, seed: int = 7, train_ratio: float = 0.7, validation_ratio: float = 0.15) -> dict:
    if not records:
        raise ValueError("no labels found")
    if not 0 < train_ratio < 1 or not 0 <= validation_ratio < 1 or train_ratio + validation_ratio >= 1:
        raise ValueError("split ratios must leave a positive test split")
    ids = set()
    hashes = set()
    groups: dict[str, list[dict]] = {}
    for record in records:
        if record["example_id"] in ids:
            raise ValueError("duplicate example_id")
        if record["screenshot_sha256"] in hashes:
            raise ValueError("duplicate screenshot hash")
        ids.add(record["example_id"]); hashes.add(record["screenshot_sha256"])
        groups.setdefault(record["capture_group_id"], []).append(record)
    group_names = sorted(groups)
    random.Random(seed).shuffle(group_names)
    n = len(group_names)
    train_end = round(n * train_ratio)
    validation_end = train_end + round(n * validation_ratio)
    if n >= 3:
        train_end = max(1, min(train_end, n - 2)); validation_end = max(train_end + 1, min(validation_end, n - 1))
    split_for_group = {g: "train" if i < train_end else "validation" if i < validation_end else "test" for i, g in enumerate(group_names)}
    entries = [{"example_id": r["example_id"], "screenshot_sha256": r["screenshot_sha256"], "capture_group_id": r["capture_group_id"], "split": split_for_group[r["capture_group_id"]], "approved_for_training": r["review"]["approved_for_training"], "eligible_for_training": r["review"]["approved_for_training"], "synthetic": r["synthetic"]} for r in sorted(records, key=lambda x: x["example_id"])]
    counts = {split: sum(e["split"] == split for e in entries) for split in ("train", "validation", "test")}
    return {"manifest_version": "phase7a-manifest-v1", "schema_version": SCHEMA_VERSION, "seed": seed, "counts": counts, "entries": entries}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    manifest = build_manifest(read_labels(args.labels), seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"counts": manifest["counts"], "manifest_version": manifest["manifest_version"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

