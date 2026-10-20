"""Local, explicit human-verification workflow for Phase 7A-compatible labels.

This module never discovers evidence. Every source is named by the caller and every
record starts ineligible. Output is intentionally limited to count-only CLI output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.training.scripts.build_manifest import SCHEMA_VERSION, build_manifest, validate_label

WORKFLOW_VERSION = "phase7f-label-workflow-v1"
FIELD_NAMES = ("phone", "message", "timestamp")
VALID_STATUSES = {"verified", "unreadable", "not_visible", "partial", "ambiguous", "truncated"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("selected JSON file is missing") from exc
    if not isinstance(payload, dict):
        raise ValueError("source must be a JSON object")
    return payload


def _validate_source(source: dict[str, Any]) -> None:
    required = {"screenshot_sha256", "capture_group_id", "image", "row", "evidence_path"}
    missing = required - source.keys()
    if missing:
        raise ValueError(f"source missing: {', '.join(sorted(missing))}")
    evidence = Path(source["evidence_path"])
    if not evidence.is_file():
        raise ValueError("explicit evidence file is missing")
    if _sha256(evidence) != source["screenshot_sha256"]:
        raise ValueError("evidence hash does not match source hash")
    candidate = dict(source)
    candidate.update({
        "schema_version": SCHEMA_VERSION, "example_id": "draft", "dataset_version": WORKFLOW_VERSION,
        "synthetic": bool(source.get("synthetic", False)),
        "fields": {name: {"status": "unreadable", "visible_text": None} for name in FIELD_NAMES},
        "review": {"approved_for_training": False, "reviewer": "draft", "status": "unreadable"},
    })
    candidate.pop("evidence_path", None)
    validate_label(candidate)


def initialize_draft(source_path: Path, output_path: Path) -> dict[str, Any]:
    source = _read(source_path)
    _validate_source(source)
    record = {
        "schema_version": SCHEMA_VERSION,
        "workflow_version": WORKFLOW_VERSION,
        "example_id": source.get("example_id") or f"draft-{uuid4().hex}",
        "screenshot_sha256": source["screenshot_sha256"],
        "source_reference": str(Path(source["evidence_path"]).resolve()),
        "capture_group_id": source["capture_group_id"],
        "image": source["image"], "row": source["row"],
        "fields": {name: {"status": "unreadable", "visible_text": None} for name in FIELD_NAMES},
        "review": {"approved_for_training": False, "reviewer": "draft", "approval_timestamp": None, "status": "unreadable"},
        "dataset_version": WORKFLOW_VERSION, "synthetic": bool(source.get("synthetic", False)),
        "audit": [{"action": "draft_created", "reviewer": None, "timestamp": _now()}],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _load_label(path: Path) -> dict[str, Any]:
    record = _read(path)
    validate_label(record)
    if record.get("workflow_version") != WORKFLOW_VERSION or not record.get("source_reference"):
        raise ValueError("not a Phase 7F workflow label")
    return record


def _save(path: Path, record: dict[str, Any]) -> None:
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def approve_label(path: Path, reviewer: str, fields: dict[str, dict[str, Any]]) -> None:
    record = _load_label(path)
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    if set(fields) != set(FIELD_NAMES):
        raise ValueError("all phone, message, and timestamp fields are required")
    for name, field in fields.items():
        if field.get("status") not in VALID_STATUSES or (field["status"] == "verified" and not isinstance(field.get("visible_text"), str)):
            raise ValueError(f"invalid {name} verification")
        if field["status"] == "ambiguous":
            raise ValueError("ambiguous fields cannot be approved")
    record["fields"] = fields
    record["review"] = {"approved_for_training": True, "reviewer": reviewer, "approval_timestamp": _now(), "status": "verified"}
    record.setdefault("audit", []).append({"action": "approved", "reviewer": reviewer, "timestamp": _now()})
    _save(path, record)


def revoke_label(path: Path, reviewer: str) -> None:
    record = _load_label(path)
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    record["review"]["approved_for_training"] = False
    record["review"]["revoked_at"] = _now()
    record["review"]["revoked_by"] = reviewer
    record.setdefault("audit", []).append({"action": "revoked", "reviewer": reviewer, "timestamp": _now()})
    _save(path, record)


def export_approved(labels_dir: Path, output_path: Path, *, seed: int = 7) -> dict[str, Any]:
    records = []
    for path in sorted(labels_dir.glob("*.json")):
        record = _load_label(path)
        if record["review"].get("approved_for_training"):
            if not Path(record["source_reference"]).is_file():
                raise ValueError("approved label evidence is missing")
            records.append(record)
    if not records:
        raise ValueError("no explicitly approved labels")
    manifest = build_manifest(records, seed=seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"manifest": manifest, "examples": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    draft = sub.add_parser("init-draft"); draft.add_argument("source", type=Path); draft.add_argument("output", type=Path)
    approve = sub.add_parser("approve"); approve.add_argument("label", type=Path); approve.add_argument("reviewer"); approve.add_argument("fields", type=Path)
    revoke = sub.add_parser("revoke"); revoke.add_argument("label", type=Path); revoke.add_argument("reviewer")
    export = sub.add_parser("export"); export.add_argument("labels", type=Path); export.add_argument("output", type=Path); export.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.command == "init-draft":
        initialize_draft(args.source, args.output); print(json.dumps({"status": "draft_created"})); return 0
    if args.command == "approve":
        approve_label(args.label, args.reviewer, _read(args.fields)); print(json.dumps({"status": "approved"})); return 0
    if args.command == "revoke":
        revoke_label(args.label, args.reviewer); print(json.dumps({"status": "revoked"})); return 0
    manifest = export_approved(args.labels, args.output, seed=args.seed)
    print(json.dumps({"status": "exported", "counts": manifest["counts"]}, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
