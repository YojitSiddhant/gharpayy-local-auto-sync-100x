import json
from pathlib import Path

import pytest

from backend.training.scripts.build_manifest import build_manifest, read_labels, validate_label


FIXTURE = Path(__file__).parents[1] / "training" / "datasets" / "synthetic" / "labels.json"


def test_synthetic_labels_validate_and_are_explicitly_marked():
    records = read_labels(FIXTURE.parent)
    assert len(records) == 3
    assert all(record["synthetic"] for record in records)
    assert all("approved_for_training" in record["review"] for record in records)


def test_invalid_schema_and_duplicate_hashes_are_rejected():
    records = read_labels(FIXTURE.parent)
    invalid = dict(records[0]); invalid.pop("fields")
    with pytest.raises(ValueError):
        validate_label(invalid)
    with pytest.raises(ValueError, match="duplicate screenshot hash"):
        build_manifest(records + [dict(records[0], example_id="another")])


def test_grouped_split_is_deterministic_and_keeps_overlaps_together():
    records = read_labels(FIXTURE.parent)
    first = build_manifest(records, seed=11)
    second = build_manifest(records, seed=11)
    assert first == second
    splits = {entry["capture_group_id"]: entry["split"] for entry in first["entries"]}
    assert len({entry["split"] for entry in first["entries"] if entry["capture_group_id"] == "synthetic-session-a"}) == 1
    assert splits["synthetic-session-a"] in {"train", "validation", "test"}


def test_manifest_contains_no_label_text_and_counts_only_are_cli_safe(tmp_path, capsys):
    from backend.training.scripts.build_manifest import main
    output = tmp_path / "manifest.json"
    import sys
    old = sys.argv
    try:
        sys.argv = ["build_manifest", str(FIXTURE.parent), str(output)]
        assert main() == 0
    finally:
        sys.argv = old
    printed = capsys.readouterr().out
    assert "Need a room" not in printed
    payload = json.loads(output.read_text())
    assert payload["counts"] == {"train": 2, "validation": 0, "test": 1}
    assert not next(entry for entry in payload["entries"] if entry["example_id"] == "synthetic-a-row-2")["eligible_for_training"]

