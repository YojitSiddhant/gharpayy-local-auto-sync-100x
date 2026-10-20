import hashlib
import json
from pathlib import Path

import pytest

from backend.training.scripts.label_workflow import approve_label, export_approved, initialize_draft, revoke_label


def source(tmp_path: Path) -> Path:
    evidence = tmp_path / "synthetic.png"; evidence.write_bytes(b"synthetic evidence")
    payload = {"screenshot_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(), "evidence_path": str(evidence), "capture_group_id": "g1", "image": {"width": 100, "height": 100, "crop": {"left": 0, "top": 0, "right": 100, "bottom": 100}}, "row": {"index": 1, "bbox": {"left": 0, "top": 0, "right": 100, "bottom": 50}}, "synthetic": True}
    path = tmp_path / "source.json"; path.write_text(json.dumps(payload)); return path


def fields():
    return {name: {"status": "verified", "visible_text": f"synthetic-{name}"} for name in ("phone", "message", "timestamp")}


def test_draft_requires_explicit_evidence_and_starts_unapproved(tmp_path):
    label = tmp_path / "label.json"; record = initialize_draft(source(tmp_path), label)
    assert record["review"]["approved_for_training"] is False
    with pytest.raises(ValueError): initialize_draft(tmp_path / "missing.json", tmp_path / "x.json")


def test_approval_revocation_and_export_exclude_unapproved(tmp_path):
    labels = tmp_path / "labels"; labels.mkdir(); label = labels / "label.json"; initialize_draft(source(tmp_path), label)
    approve_label(label, "reviewer-a", fields())
    exported = tmp_path / "export.json"; manifest = export_approved(labels, exported)
    assert manifest["counts"]["train"] + manifest["counts"]["test"] + manifest["counts"]["validation"] == 1
    revoke_label(label, "reviewer-a")
    with pytest.raises(ValueError, match="no explicitly approved"):
        export_approved(labels, tmp_path / "empty.json")


def test_ambiguous_and_duplicate_labels_are_rejected(tmp_path):
    labels = tmp_path / "labels"; labels.mkdir(); label = labels / "label.json"; initialize_draft(source(tmp_path), label)
    bad = fields(); bad["phone"] = {"status": "ambiguous", "visible_text": None}
    with pytest.raises(ValueError): approve_label(label, "reviewer-a", bad)
    approve_label(label, "reviewer-a", fields())
    duplicate = labels / "duplicate.json"; duplicate.write_text(label.read_text().replace('"example_id": "draft-', '"example_id": "other-'))
    with pytest.raises(ValueError, match="duplicate screenshot hash"):
        export_approved(labels, tmp_path / "duplicate-export.json")
