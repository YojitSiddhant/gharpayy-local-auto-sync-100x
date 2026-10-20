"""Local, fail-closed registry for Phase 7D model artifacts.

Registry updates are explicit operator actions. This module never changes runtime
configuration, OCR behavior, evidence, or database state.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.ml.classification.row_classifier import load_row_classifier
from backend.app.ml.features.ocr_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, feature_schema_fingerprint

REGISTRY_VERSION = "phase7g-model-registry-v1"
LIFECYCLE_STATES = {"OFF", "SHADOW", "EVALUATED", "ENABLED"}
DEFAULT_THRESHOLDS = {"phone_precision": 0.95, "wrong_association_rate": 0.05, "abstention_rate": 0.50, "latency_ms": 1000.0, "memory_mb": 1024.0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted(path: Path, root: Path) -> Path:
    candidate, trusted = path.resolve(), root.resolve()
    if trusted != candidate and trusted not in candidate.parents:
        raise ValueError("artifact is outside trusted model directory")
    return candidate


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"registry_version": REGISTRY_VERSION, "entries": [], "selection": {"shadow_model_id": None}, "audit": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("registry file is corrupt") from exc
    if not isinstance(payload, dict) or payload.get("registry_version") != REGISTRY_VERSION or not isinstance(payload.get("entries"), list):
        raise ValueError("registry schema is incompatible")
    return payload


def _write_locked(path: Path, update) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        payload = _load_registry(path)
        result = update(payload)
        fd, temp_name = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(payload, out, indent=2, sort_keys=True); out.write("\n"); out.flush(); os.fsync(out.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return result


def _safe_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return only numeric gate fields and provenance flags; discard arbitrary report data."""
    metrics = report.get("metrics", report)
    required = ("phone_precision", "wrong_association_rate", "abstention_rate", "latency_ms", "memory_mb")
    result: dict[str, Any] = {key: metrics[key] for key in required if isinstance(metrics.get(key), (int, float))}
    result["independent_holdout"] = report.get("independent_holdout") is True
    result["synthetic_only"] = report.get("synthetic_only") is True
    result["approved_dataset"] = report.get("approved_dataset") is True
    result["association_regression"] = report.get("association_regression", True)
    result["dataset_version"] = str(report.get("dataset_version", ""))
    return result


def eligibility(report: dict[str, Any], *, thresholds: dict[str, float] | None = None) -> tuple[bool, list[str], dict[str, Any]]:
    metrics = _safe_report(report); limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}; reasons: list[str] = []
    if metrics["synthetic_only"]: reasons.append("synthetic-only evaluation")
    if not metrics["independent_holdout"]: reasons.append("independent holdout evidence is missing")
    if not metrics["approved_dataset"]: reasons.append("approved dataset evidence is missing")
    if metrics["association_regression"] is not False: reasons.append("association regression is not cleared")
    for key in ("phone_precision", "wrong_association_rate", "abstention_rate", "latency_ms", "memory_mb"):
        if key not in metrics: reasons.append(f"missing metric: {key}")
    if "phone_precision" in metrics and metrics["phone_precision"] < limits["phone_precision"]: reasons.append("phone precision below gate")
    for key in ("wrong_association_rate", "abstention_rate", "latency_ms", "memory_mb"):
        if key in metrics and metrics[key] > limits[key]: reasons.append(f"{key} above gate")
    return not reasons, reasons, metrics


def register_model(registry: Path, artifact_dir: Path, trusted_root: Path, *, model_id: str, algorithm: str, dataset_version: str, evaluation_path: Path, seed: int, source_revision: str = "unknown") -> dict[str, Any]:
    artifact = _trusted(artifact_dir, trusted_root)
    # Reuse Phase 7D's complete artifact/schema/checksum validation.
    classifier = load_row_classifier(artifact, trusted_root=trusted_root)
    report = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict): raise ValueError("evaluation report must be an object")
    eligible, reasons, safe_metrics = eligibility(report)
    entry = {"model_id": model_id, "algorithm": algorithm, "artifact_path": str(artifact), "artifact_sha256": _sha256(artifact / "model.joblib"), "feature_schema_version": FEATURE_SCHEMA_VERSION, "feature_schema_fingerprint": feature_schema_fingerprint(), "feature_count": len(FEATURE_NAMES), "dataset_version": dataset_version, "training": {"seed": seed, "python": platform.python_version(), "platform": platform.platform()}, "source_revision": source_revision, "evaluation": {"reference": str(evaluation_path.resolve()), "metrics": safe_metrics, "eligible": eligible, "reasons": reasons}, "lifecycle_status": "EVALUATED" if eligible else "OFF", "previous_model_version": None, "created_at": _now(), "approved_at": None, "model_artifact_version": classifier.metadata.get("artifact_version")}
    def update(payload: dict[str, Any]) -> dict[str, Any]:
        if any(item.get("model_id") == model_id for item in payload["entries"]): raise ValueError("duplicate model version")
        payload["entries"].append(entry); payload["audit"].append({"action": "registered", "model_id": model_id, "timestamp": _now()}); return entry
    return _write_locked(registry, update)


def validate_model(registry: Path, model_id: str, trusted_root: Path) -> dict[str, Any]:
    payload = _load_registry(registry); entry = next((x for x in payload["entries"] if x.get("model_id") == model_id), None)
    if entry is None: raise ValueError("model version not found")
    artifact = _trusted(Path(entry["artifact_path"]), trusted_root)
    load_row_classifier(artifact, trusted_root=trusted_root)
    if _sha256(artifact / "model.joblib") != entry["artifact_sha256"]: raise ValueError("registered artifact checksum mismatch")
    return {"model_id": model_id, "valid": True}


def select_shadow(registry: Path, model_id: str, trusted_root: Path, operator: str) -> None:
    validate_model(registry, model_id, trusted_root)
    def update(payload: dict[str, Any]) -> None:
        target = next(x for x in payload["entries"] if x["model_id"] == model_id)
        if not target["evaluation"]["eligible"]: raise ValueError("model is not eligible for shadow selection")
        previous = payload.get("selection", {}).get("shadow_model_id")
        for item in payload["entries"]:
            if item["lifecycle_status"] == "SHADOW": item["lifecycle_status"] = "OFF"
        target["lifecycle_status"] = "SHADOW"; target["previous_model_version"] = previous
        payload.setdefault("selection", {})["shadow_model_id"] = model_id
        payload["audit"].append({"action": "shadow_selected", "model_id": model_id, "operator": operator, "timestamp": _now()})
    _write_locked(registry, update)


def rollback(registry: Path, target_id: str, trusted_root: Path, operator: str, reason: str) -> None:
    validate_model(registry, target_id, trusted_root)
    if not reason.strip(): raise ValueError("rollback reason is required")
    def update(payload: dict[str, Any]) -> None:
        target = next(x for x in payload["entries"] if x["model_id"] == target_id)
        if not target["evaluation"]["eligible"]: raise ValueError("rollback target is not eligible")
        old = payload.get("selection", {}).get("shadow_model_id")
        for item in payload["entries"]:
            if item["lifecycle_status"] == "SHADOW": item["lifecycle_status"] = "OFF"
        target["lifecycle_status"] = "SHADOW"; payload.setdefault("selection", {})["shadow_model_id"] = target_id
        payload["audit"].append({"action": "rollback", "from_model_id": old, "to_model_id": target_id, "operator": operator, "reason": reason, "timestamp": _now()})
    _write_locked(registry, update)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register"); reg.add_argument("registry", type=Path); reg.add_argument("artifact", type=Path); reg.add_argument("trusted_root", type=Path); reg.add_argument("model_id"); reg.add_argument("evaluation", type=Path); reg.add_argument("--algorithm", default="unknown"); reg.add_argument("--dataset-version", default="unknown"); reg.add_argument("--seed", type=int, default=7); reg.add_argument("--source-revision", default="unknown")
    listing = sub.add_parser("list"); listing.add_argument("registry", type=Path)
    val = sub.add_parser("validate"); val.add_argument("registry", type=Path); val.add_argument("model_id"); val.add_argument("trusted_root", type=Path)
    elig = sub.add_parser("eligibility"); elig.add_argument("evaluation", type=Path)
    sel = sub.add_parser("select-shadow"); sel.add_argument("registry", type=Path); sel.add_argument("model_id"); sel.add_argument("trusted_root", type=Path); sel.add_argument("operator")
    rb = sub.add_parser("rollback"); rb.add_argument("registry", type=Path); rb.add_argument("target_id"); rb.add_argument("trusted_root", type=Path); rb.add_argument("operator"); rb.add_argument("reason")
    args = parser.parse_args()
    if args.command == "register": register_model(args.registry, args.artifact, args.trusted_root, model_id=args.model_id, algorithm=args.algorithm, dataset_version=args.dataset_version, evaluation_path=args.evaluation, seed=args.seed, source_revision=args.source_revision); print(json.dumps({"status": "registered", "model_id": args.model_id})); return 0
    if args.command == "list":
        payload = _load_registry(args.registry); print(json.dumps({"models": [{"model_id": x["model_id"], "status": x["lifecycle_status"], "eligible": x["evaluation"]["eligible"]} for x in payload["entries"]], "shadow_model_id": payload.get("selection", {}).get("shadow_model_id")}, sort_keys=True)); return 0
    if args.command == "validate": print(json.dumps(validate_model(args.registry, args.model_id, args.trusted_root))); return 0
    if args.command == "eligibility":
        report = json.loads(args.evaluation.read_text(encoding="utf-8")); ok, reasons, _ = eligibility(report); print(json.dumps({"eligible": ok, "reasons": reasons}, sort_keys=True)); return 0
    if args.command == "select-shadow": select_shadow(args.registry, args.model_id, args.trusted_root, args.operator); print(json.dumps({"status": "shadow_selected", "model_id": args.model_id})); return 0
    rollback(args.registry, args.target_id, args.trusted_root, args.operator, args.reason); print(json.dumps({"status": "rolled_back", "model_id": args.target_id})); return 0


if __name__ == "__main__": raise SystemExit(main())
