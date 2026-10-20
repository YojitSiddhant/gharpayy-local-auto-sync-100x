"""Phase 4: optimistic concurrency, storage consistency and request-security hardening."""
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.errors import ApiError
from backend.app.db.session import engine
from backend.app.models import EvidenceAuditEvent, OcrResult, OcrTextCorrection, ScreenshotCapture
from backend.app.ocr import evidence as ev
from backend.app.ocr import storage_check
from backend.app.ocr.service import process_screenshot
from backend.app.ocr.image_validation import ImageLimits, validate_image
from backend.app.ocr.storage import store_evidence
from backend.tests.conftest import TEST_TOKEN, FakeEngine, chat_list_image, mysql_unavailable, upload_headers

UPLOAD = "/api/v1/ocr/extract"
EV = "/api/v1/evidence"
AUTH = {"x-local-api-token": TEST_TOKEN}


def upload(client, data=None, account="Test WhatsApp") -> str:
    return client.post(UPLOAD, content=data or chat_list_image(), headers=upload_headers(account=account)).json()["capture"]["id"]


def detail(client, capture_id):
    return client.get(f"{EV}/{capture_id}", headers=AUTH).json()


# --- A. concurrent editing ------------------------------------------------------------

def test_second_tab_with_stale_revision_cannot_overwrite(db_api):
    capture_id = upload(db_api)
    base = detail(db_api, capture_id)["revision"]  # both tabs loaded this revision
    tab_a = db_api.patch(f"{EV}/{capture_id}", json={"text": "tab A text", "reviewer": "A", "expected_revision": base}, headers=AUTH)
    tab_b = db_api.patch(f"{EV}/{capture_id}", json={"text": "tab B text", "reviewer": "B", "expected_revision": base}, headers=AUTH)
    assert tab_a.status_code == 200 and tab_b.status_code == 409
    error = tab_b.json()["error"]
    assert error["code"] == "EDIT_CONFLICT" and error["details"] == {"current_revision": base + 1, "expected_revision": base}
    saved = detail(db_api, capture_id)
    assert saved["text"] == "tab A text" and [c["reviewer"] for c in saved["corrections"]] == ["A"]
    # Tab B re-bases explicitly on the latest revision; history stays a consistent chain.
    retry = db_api.patch(f"{EV}/{capture_id}", json={"text": "tab B text", "reviewer": "B", "expected_revision": saved["revision"]}, headers=AUTH).json()
    assert [(c["old_text"], c["new_text"]) for c in retry["corrections"]][-1] == ("tab A text", "tab B text")
    assert "private room" in retry["original_text"]


def test_expected_revision_is_required_for_edits_and_review(db_api):
    capture_id = upload(db_api)
    missing = db_api.patch(f"{EV}/{capture_id}", json={"text": "x"}, headers=AUTH)
    assert missing.status_code == 422 and missing.json()["error"]["details"]["fields"] == ["expected_revision"]
    assert db_api.post(f"{EV}/{capture_id}/review", json={}, headers=AUTH).status_code == 422


def test_review_of_a_stale_view_is_rejected_without_audit(db_api, mysql_session):
    capture_id = upload(db_api)
    stale = detail(db_api, capture_id)["revision"]
    db_api.patch(f"{EV}/{capture_id}", json={"text": "changed elsewhere", "expected_revision": stale}, headers=AUTH)
    response = db_api.post(f"{EV}/{capture_id}/review", json={"expected_revision": stale}, headers=AUTH)
    assert response.json()["error"]["code"] == "EDIT_CONFLICT"
    assert detail(db_api, capture_id)["status"] == "review"
    assert mysql_session.scalars(select(EvidenceAuditEvent).where(EvidenceAuditEvent.capture_id == capture_id, EvidenceAuditEvent.action == "review")).all() == []


def test_every_mutation_advances_the_revision(db_api):
    capture_id = upload(db_api)
    r0 = detail(db_api, capture_id)["revision"]
    db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "r"}, headers=AUTH)
    r1 = detail(db_api, capture_id)["revision"]
    db_api.post(f"{EV}/{capture_id}/restore", json={"reason": "r", "expected_revision": r1}, headers=AUTH)
    r2 = detail(db_api, capture_id)["revision"]
    db_api.post(f"{EV}/{capture_id}/proposals", json={"kind": "label", "region": {"x0": 0, "y0": 0, "x1": 720, "y1": 1280}}, headers=AUTH)
    r3 = detail(db_api, capture_id)["revision"]
    assert r0 < r1 < r2 < r3
    stale_trash = db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "r", "expected_revision": r0}, headers=AUTH)
    assert stale_trash.json()["error"]["code"] == "EDIT_CONFLICT"


def test_rejected_edit_leaves_no_partial_change(db_api):
    capture_id = upload(db_api)
    before = detail(db_api, capture_id)
    bad = db_api.patch(f"{EV}/{capture_id}", json={"text": "should not persist", "observed_names": [{"position": 99, "name": "x"}],
                                                   "expected_revision": before["revision"]}, headers=AUTH)
    assert bad.json()["error"]["code"] == "INVALID_ROW"
    after = detail(db_api, capture_id)
    assert after["revision"] == before["revision"] and after["text"] == before["text"] and after["corrections"] == []


def test_truly_concurrent_edits_from_two_connections_cannot_both_win():
    """Two threads, two real MySQL connections, same base revision: exactly one wins, one conflicts."""
    try:
        with engine.connect() as connection:
            columns = {c["name"] for c in inspect(connection).get_columns("screenshot_captures")}
    except SQLAlchemyError:
        mysql_unavailable("MySQL not reachable")
    if "revision" not in columns:
        mysql_unavailable("migration 20261020_0003 not applied")
    label = f"pytest-concurrency-{uuid.uuid4()}"
    with Session(engine) as setup:
        capture = ScreenshotCapture(sha256=uuid.uuid4().hex * 2, account_label=label, mime_type="image/png", byte_size=1,
                                    width=10, height=10, status="completed")
        capture.results.append(OcrResult(attempt=1, status="completed", tesseract_psm=11, raw_text="original"))
        setup.add(capture)
        setup.commit()
        capture_id, base = capture.id, capture.revision
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def edit(text: str) -> None:
        with Session(engine) as db:
            target = db.get(ScreenshotCapture, capture_id)
            barrier.wait()
            try:
                ev.update_evidence(db, target, reviewer=text, screenshot_label=None, text=text, observed_names=None, expected_revision=base)
                outcomes.append("saved")
            except ApiError as exc:
                outcomes.append(exc.code)

    try:
        threads = [threading.Thread(target=edit, args=(t,)) for t in ("first", "second")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert sorted(outcomes) == ["EDIT_CONFLICT", "saved"]
        with Session(engine) as check:
            saved = check.get(ScreenshotCapture, capture_id)
            corrections = check.scalars(select(OcrTextCorrection).where(OcrTextCorrection.capture_id == capture_id)).all()
            assert saved.revision == base + 1 and len(corrections) == 1 and saved.corrected_text == corrections[0].new_text
    finally:
        with Session(engine) as cleanup:  # remove only the row this test created
            cleanup.execute(delete(ScreenshotCapture).where(ScreenshotCapture.account_label == label))
            cleanup.commit()


# --- B. evidence storage --------------------------------------------------------------

def test_storage_write_failure_is_visible_and_ocr_still_completes(db_api, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("backend.app.ocr.service.store_evidence", broken)
    body = db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers()).json()
    assert body["capture"]["evidence_stored"] is False and body["capture"]["status"] == "completed"
    assert any("could not be retained on disk" in w for w in body["result"]["warnings"])


def test_evidence_file_is_linked_before_ocr_runs(mysql_session, test_settings):
    """A crash during OCR must not orphan the file: the key is committed before OCR starts."""
    data = chat_list_image()
    limits = ImageLimits(test_settings.ocr_max_upload_bytes, test_settings.ocr_max_image_pixels, test_settings.ocr_max_image_side, test_settings.ocr_min_image_side)
    image = validate_image(data, "image/png", limits)
    crash = FakeEngine(error=KeyboardInterrupt())  # not an Exception: simulates the process dying mid-OCR
    with pytest.raises(KeyboardInterrupt):
        process_screenshot(mysql_session, image, "Test WhatsApp", "crash.png", test_settings, lambda s: crash)
    mysql_session.rollback()
    capture = mysql_session.scalar(select(ScreenshotCapture).where(ScreenshotCapture.sha256 == image.sha256))
    assert capture.status == "processing" and capture.evidence_storage_key == f"{image.sha256}.png"


def test_store_evidence_replaces_truncated_or_symlinked_files(tmp_path):
    data = chat_list_image()
    sha = __import__("hashlib").sha256(data).hexdigest()
    key = store_evidence(tmp_path, sha, "png", data)
    (tmp_path / key).write_bytes(data[:10])  # truncated by an earlier failure
    store_evidence(tmp_path, sha, "png", data)
    assert (tmp_path / key).read_bytes() == data
    (tmp_path / key).unlink()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not evidence")
    (tmp_path / key).symlink_to(outside)
    store_evidence(tmp_path, sha, "png", data)
    assert not (tmp_path / key).is_symlink() and (tmp_path / key).read_bytes() == data and outside.read_bytes() == b"not evidence"


def test_storage_check_reports_and_removes_only_unreferenced_files(db_api, mysql_session, test_settings):
    kept = upload(db_api)
    root = test_settings.screenshot_storage_dir
    referenced_key = mysql_session.get(ScreenshotCapture, kept).evidence_storage_key
    orphan = os.path.join(root, "f" * 64 + ".png")
    open(orphan, "wb").write(b"orphan")
    stale_temp = os.path.join(root, ".incoming-abc")
    open(stale_temp, "wb").write(b"partial")
    os.utime(stale_temp, (time.time() - 7200, time.time() - 7200))
    fresh_temp = os.path.join(root, ".incoming-new")
    open(fresh_temp, "wb").write(b"in progress")
    open(os.path.join(root, "notes.txt"), "w").write("unexpected")
    # A record whose file went missing.
    missing = ScreenshotCapture(sha256="9" * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=1, width=10, height=10,
                                status="completed", evidence_storage_key="9" * 64 + ".png")
    mysql_session.add(missing)
    mysql_session.commit()

    report = storage_check.check_storage(mysql_session, root)
    assert report.summary() == {"referenced_present": 1, "missing_files": 1, "orphan_files": 1, "stale_temp_files": 1, "unexpected_entries": 1}
    assert os.path.exists(orphan)  # the check alone deletes nothing
    assert storage_check.remove_orphans(mysql_session, root, report) == 2
    assert not os.path.exists(orphan) and not os.path.exists(stale_temp)
    assert os.path.exists(fresh_temp) and os.path.exists(os.path.join(root, "notes.txt")) and os.path.exists(os.path.join(root, referenced_key))
    assert mysql_session.get(ScreenshotCapture, missing.id) is not None  # database rows are never touched


def test_storage_check_never_removes_a_file_referenced_after_the_scan(db_api, mysql_session, test_settings):
    root = test_settings.screenshot_storage_dir
    os.makedirs(root, exist_ok=True)
    data = chat_list_image()
    sha = __import__("hashlib").sha256(data).hexdigest()
    open(os.path.join(root, f"{sha}.png"), "wb").write(data)
    report = storage_check.check_storage(mysql_session, root)
    assert len(report.orphan_files) == 1
    upload(db_api, data)  # a new upload now references the same content-addressed file
    assert storage_check.remove_orphans(mysql_session, root, report) == 0
    assert os.path.exists(os.path.join(root, f"{sha}.png"))


def test_symlinked_evidence_is_never_served(db_api, mysql_session, test_settings, tmp_path):
    capture_id = upload(db_api)
    key = mysql_session.get(ScreenshotCapture, capture_id).evidence_storage_key
    path = os.path.join(test_settings.screenshot_storage_dir, key)
    secret = tmp_path / "secret.txt"
    secret.write_text("private file outside the evidence store")
    os.remove(path)
    os.symlink(secret, path)
    response = db_api.get(f"{EV}/{capture_id}/image", headers=AUTH)
    assert response.status_code == 404 and "private file" not in response.text


def test_retention_dry_run_matches_apply_exactly(mysql_session):
    from datetime import timedelta
    from backend.app.models.ocr import utcnow
    from backend.app.ocr import retention
    for i, (days, status) in enumerate([(40, "completed"), (40, "failed"), (40, "processing"), (5, "completed")]):
        mysql_session.add(ScreenshotCapture(sha256=str(i) * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=1,
                                            width=10, height=10, status=status, captured_at=utcnow() - timedelta(days=days)))
    mysql_session.commit()
    planned = retention.plan(mysql_session, 30)
    assert planned == {"total": 2, "review": 1, "error": 1}  # in-flight and recent evidence are excluded
    assert retention.apply(mysql_session, 30) == planned["total"]


# --- C. request security ------------------------------------------------------------------

def test_forwarded_for_header_cannot_bypass_the_loopback_check(test_settings):
    from fastapi.testclient import TestClient
    from backend.app.core.config import get_settings
    from backend.app.main import app
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"app_env": "development"})
    try:
        response = TestClient(app).get(EV, headers={**AUTH, "x-forwarded-for": "127.0.0.1", "x-real-ip": "127.0.0.1"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403 and response.json()["error"]["code"] == "LOOPBACK_ONLY"


def test_errors_never_echo_tokens_paths_or_submitted_text(db_api, test_settings):
    capture_id = upload(db_api)
    secret_text = "PRIVATE-CUSTOMER-MESSAGE-123"
    bad = db_api.patch(f"{EV}/{capture_id}", json={"text": secret_text, "expected_revision": "not-a-number"}, headers=AUTH)
    assert bad.status_code == 422 and secret_text not in bad.text
    for response in (bad, db_api.get(f"{EV}/{capture_id}", headers=AUTH), db_api.get(f"{EV}/export", headers=AUTH)):
        assert TEST_TOKEN not in response.text and test_settings.screenshot_storage_dir not in response.text


# --- F. benchmark harness ---------------------------------------------------------------

@pytest.mark.skipif(__import__("shutil").which("tesseract") is None, reason="Tesseract executable not installed")
def test_benchmark_is_bounded_and_accounts_duplicates_separately():
    from backend.benchmarks import ocr_benchmark
    report = ocr_benchmark.run(count=5, duplicate_every=2, with_db=False)
    assert report["images"] == 5 and report["unique"] + report["exact_duplicates"] == 5 and report["exact_duplicates"] == 2
    assert report["failures"] == 0 and report["latency"]["ocr"]["avg_ms"] > 0
    assert "DERIVED" in report["derived_sequential_capacity"]["note"] and "MEASURED" in report["label"]
    with pytest.raises(SystemExit):
        ocr_benchmark.main(["--count", str(ocr_benchmark.MAX_COUNT + 1)])
