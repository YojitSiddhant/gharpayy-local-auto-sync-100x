"""Saved-evidence workflow against MySQL (rolled back): list, detail, image, review, corrections, trash, purge, export."""
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.main import app
from backend.app.models import EvidenceAuditEvent
from backend.app.ocr.engine import OcrTimeoutError
from backend.tests.conftest import TEST_TOKEN, chat_list_image, image_bytes, make_line, upload_headers

UPLOAD = "/api/v1/ocr/extract"
EV = "/api/v1/evidence"
AUTH = {"x-local-api-token": TEST_TOKEN}


MIME = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


def upload(client, data=None, name="synthetic-chat.png", account="Test WhatsApp", **extra):
    data = data or chat_list_image()
    mime = next(m for sig, m in MIME.items() if data.startswith(sig))
    response = client.post(UPLOAD, content=data, headers=upload_headers(mime, account=account, **{"x-filename": name, **extra}))
    assert response.status_code in (200, 201), response.text
    return response.json()["capture"]["id"]


def items(client, **params):
    return client.get(EV, params=params, headers=AUTH).json()["items"]


def test_saved_evidence_survives_reload_and_lists_newest_first(db_api):
    first = upload(db_api, name="first.png")
    second = upload(db_api, data=chat_list_image("JPEG"), name="second.jpg")
    listed = items(db_api)  # a fresh page load only has this list call
    assert [i["id"] for i in listed][:2] == [second, first]
    assert listed[0] == {**listed[0], "label": "second.jpg", "status": "review", "trashed": False}


def test_detail_preserves_original_text_and_rows(db_api):
    capture_id = upload(db_api)
    detail = db_api.get(f"{EV}/{capture_id}", headers=AUTH).json()
    assert detail["text"] == detail["original_text"] and "Need a private room" in detail["original_text"]
    assert detail["identity_status"] == "unverified" and detail["review_status"] == "unverified_review_only"
    assert detail["rows"][0]["phone"]["normalized"] == "+919876543210"
    assert detail["source_mode"] == "manual-file"
    assert "storage" not in str(detail).lower() or "evidence_stored" in detail  # no file paths are exposed
    assert not any(isinstance(v, str) and "private_storage" in v for v in detail.values())


def test_protected_image_retrieval(db_api):
    data = chat_list_image()
    capture_id = upload(db_api, data=data)
    image = db_api.get(f"{EV}/{capture_id}/image", headers=AUTH)
    assert image.status_code == 200 and image.content == data
    assert image.headers["content-type"] == "image/png" and image.headers["x-content-type-options"] == "nosniff"
    assert db_api.get(f"{EV}/{capture_id}/image").status_code == 401


def test_image_not_retained_returns_404(db_api, test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"screenshot_retention_enabled": False})
    capture_id = upload(db_api)
    assert db_api.get(f"{EV}/{capture_id}/image", headers=AUTH).json()["error"]["code"] == "EVIDENCE_IMAGE_NOT_RETAINED"


def test_corrections_append_history_and_reopen_review(db_api):
    capture_id = upload(db_api)
    original = db_api.get(f"{EV}/{capture_id}", headers=AUTH).json()["original_text"]
    db_api.post(f"{EV}/{capture_id}/review", json={"reviewer": "R1"}, headers=AUTH)
    first = db_api.patch(f"{EV}/{capture_id}", json={"text": "edited", "reviewer": "R1"}, headers=AUTH).json()
    second = db_api.patch(f"{EV}/{capture_id}", json={"text": "final", "reviewer": ""}, headers=AUTH).json()
    same = db_api.patch(f"{EV}/{capture_id}", json={"text": "final"}, headers=AUTH).json()
    assert second["original_text"] == original and second["text"] == "final"
    assert [(c["old_text"], c["new_text"], c["reviewer"]) for c in second["corrections"]] == [
        (original, "edited", "R1"), ("edited", "final", "Unidentified local reviewer")]
    assert len(same["corrections"]) == 2
    assert first["status"] == "review"  # a correction re-opens review; identity never verified
    assert second["identity_status"] == "unverified"


def test_label_and_observed_names_and_association_quarantine(db_api, fake_engine):
    a = upload(db_api, name="a.png")
    fake_engine.lines = [make_line("+91 94491 90529", 140, 180), make_line("3:10 pm", 560, 184), make_line("hello", 140, 228)]
    b = upload(db_api, data=image_bytes("PNG", (720, 1280), "white"), name="b.png")
    db_api.patch(f"{EV}/{a}", json={"screenshot_label": "Morning batch", "observed_names": [{"position": 1, "name": "Alex"}]}, headers=AUTH)
    detail_a = db_api.get(f"{EV}/{a}", headers=AUTH).json()
    assert detail_a["label"] == "Morning batch" and detail_a["rows"][0]["observed_name"] == "Alex"
    # Same reviewer name on a different phone elsewhere → quarantined association.
    db_api.patch(f"{EV}/{b}", json={"observed_names": [{"position": 1, "name": "alex"}]}, headers=AUTH)
    detail_a = db_api.get(f"{EV}/{a}", headers=AUTH).json()
    assert any("Alex" in c and "quarantined" in c for c in detail_a["conflicts"])
    assert db_api.patch(f"{EV}/{a}", json={"observed_names": [{"position": 99, "name": "x"}]}, headers=AUTH).status_code == 400


def test_mark_reviewed_rules(db_api):
    capture_id = upload(db_api)
    reviewed = db_api.post(f"{EV}/{capture_id}/review", json={"reviewer": "R"}, headers=AUTH).json()
    assert reviewed["status"] == "reviewed" and reviewed["identity_status"] == "unverified"
    assert db_api.post(f"{EV}/{capture_id}/review", json={}, headers=AUTH).status_code == 409
    assert [i["id"] for i in items(db_api, status="reviewed")] == [capture_id]
    assert items(db_api, status="review") == []


def test_trash_restore_history_and_filters(db_api, mysql_session):
    capture_id = upload(db_api)
    blank = db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "   "}, headers=AUTH)
    assert blank.status_code == 400 and blank.json()["error"]["code"] == "REASON_REQUIRED"
    assert db_api.post(f"{EV}/{capture_id}/trash", json={}, headers=AUTH).json()["error"]["code"] == "INVALID_REQUEST"
    trashed = db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "Wrong input", "reviewer": "R"}, headers=AUTH).json()
    assert trashed["trash"]["reason"] == "Wrong input"
    assert items(db_api) == [] and [i["id"] for i in items(db_api, status="trash")] == [capture_id]
    assert db_api.patch(f"{EV}/{capture_id}", json={"text": "x"}, headers=AUTH).status_code == 409
    assert db_api.post(f"{EV}/{capture_id}/review", json={}, headers=AUTH).status_code == 409
    restored = db_api.post(f"{EV}/{capture_id}/restore", json={"reason": "Keep evidence", "reviewer": "R"}, headers=AUTH).json()
    assert restored["trash"] is None and restored["text"] == restored["original_text"]
    assert [h["action"] for h in restored["trash_history"]] == ["trash", "restore"]


def test_purge_requires_confirmation_and_removes_only_trash(db_api, mysql_session, test_settings):
    keep = upload(db_api, name="keep.png")
    gone = upload(db_api, data=chat_list_image("WEBP"), name="gone.webp")
    gone_key = Path(test_settings.screenshot_storage_dir)
    assert db_api.post(f"{EV}/purge-trash", json={}, headers=AUTH).status_code == 422
    db_api.post(f"{EV}/{gone}/trash", json={"reason": "duplicate input"}, headers=AUTH)
    result = db_api.post(f"{EV}/purge-trash", json={"confirm": True, "reviewer": "R"}, headers=AUTH).json()
    assert result == {"purged": 1}
    assert db_api.get(f"{EV}/{gone}", headers=AUTH).status_code == 404
    assert db_api.get(f"{EV}/{keep}", headers=AUTH).status_code == 200
    assert len(list(gone_key.glob("*.webp"))) == 0 and len(list(gone_key.glob("*.png"))) == 1
    purge_events = mysql_session.scalars(select(EvidenceAuditEvent).where(EvidenceAuditEvent.capture_id == gone, EvidenceAuditEvent.action == "purge")).all()
    assert len(purge_events) == 1 and purge_events[0].reason and "Need a private" not in purge_events[0].reason


def test_search_by_filename_phone_and_text(db_api):
    upload(db_api, name="alpha.png")
    upload(db_api, data=image_bytes("PNG", (720, 1280)), name="blank.png")
    assert {i["label"] for i in items(db_api, search="ALPHA")} == {"alpha.png"}
    assert {i["label"] for i in items(db_api, search="98765 43210")} == {"alpha.png", "blank.png"}  # fake OCR lines are shared
    assert items(db_api, search="no such words") == []
    assert db_api.get(EV, params={"status": "bogus"}, headers=AUTH).status_code == 400


def test_stats_and_duplicate_count_are_real(db_api):
    data = chat_list_image()
    capture_id = upload(db_api, data=data)
    upload(db_api, data=data)
    stats = db_api.get(f"{EV}/stats", headers=AUTH).json()
    assert stats["review"] == 1 and stats["reviewed"] == 0 and stats["failed"] == 0
    assert stats["duplicates"] == 1 and stats["records"] == 1 and stats["stored_bytes"] == len(data)
    assert stats["max_records"] == 200 and stats["average_ms"] == 5
    db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "r"}, headers=AUTH)
    assert db_api.get(f"{EV}/stats", headers=AUTH).json()["trash"] == 1


def test_export_excludes_trash_and_image_bytes(db_api):
    keep = upload(db_api)
    gone = upload(db_api, data=chat_list_image("JPEG"))
    db_api.post(f"{EV}/{gone}/trash", json={"reason": "r"}, headers=AUTH)
    exported = db_api.get(f"{EV}/export", headers=AUTH)
    assert "attachment" in exported.headers["content-disposition"]
    body = exported.json()
    assert [e["id"] for e in body] == [keep]
    assert body[0]["association_review"]["identity_status"] == "unverified"
    assert "image" not in body[0] and "base64" not in exported.text
    assert len(db_api.get(f"{EV}/export", params={"include_trash": True}, headers=AUTH).json()) == 2


def test_retry_failed_ocr_from_stored_evidence(db_api, fake_engine):
    fake_engine.error = OcrTimeoutError("slow")
    failed = db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers())
    capture_id = failed.json()["error"]["details"]["capture_id"]
    assert [i["status"] for i in items(db_api, status="error")] == ["error"]
    fake_engine.error = None
    retried = db_api.post(f"{EV}/{capture_id}/retry", json={"reviewer": "R"}, headers=AUTH).json()
    assert retried["status"] == "review" and retried["result"]["attempt"] == 2
    assert db_api.post(f"{EV}/{capture_id}/retry", json={}, headers=AUTH).status_code == 409


def test_region_proposals_are_tentative(db_api):
    capture_id = upload(db_api)
    bad = db_api.post(f"{EV}/{capture_id}/proposals", json={"kind": "label", "region": {"x0": 10, "y0": 10, "x1": 5, "y1": 5}}, headers=AUTH)
    assert bad.status_code == 400
    detail = db_api.post(f"{EV}/{capture_id}/proposals", json={"kind": "unread", "region": {"x0": 550, "y0": 170, "x1": 720, "y1": 230}}, headers=AUTH).json()
    proposal = detail["proposals"][0]
    assert proposal["text"] == "2:52 pm" and proposal["status"] == "tentative" and proposal["color_meaning"] == "unknown"
    assert proposal["count"] is None and proposal["color"].startswith("rgb(")


def test_screen_capture_metadata_is_preserved(db_api):
    capture_id = upload(db_api, **{"x-source-mode": "screen-capture", "x-captured-at": "2026-10-19T10:00:00.000Z",
                                   "x-original-width": "1440", "x-original-height": "1280", "x-crop": "0,0,720,1280",
                                   "x-source-label": "screen%3A1", "x-filename": "capture-1.png"})
    detail = db_api.get(f"{EV}/{capture_id}", headers=AUTH).json()
    assert detail["source_mode"] == "screen-capture" and detail["source_label"] == "screen:1"
    assert detail["original_dimensions"] == {"width": 1440, "height": 1280}
    assert detail["crop"] == {"x0": 0, "y0": 0, "x1": 720, "y1": 1280}
    assert detail["captured_at"].startswith("2026-10-19T10:00:00")


def test_invalid_capture_metadata_rejected(db_api):
    response = db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers(**{"x-crop": "0,0,900,10", "x-original-width": "800", "x-original-height": "600"}))
    assert response.status_code == 400
    response = db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers(**{"x-source-mode": "auto-magic"}))
    assert response.status_code == 400


def test_storage_full_pauses_new_evidence(db_api, test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"evidence_max_records": 1})
    upload(db_api)
    full = db_api.post(UPLOAD, content=chat_list_image("JPEG"), headers=upload_headers("image/jpeg"))
    assert full.status_code == 507 and full.json()["error"]["code"] == "STORAGE_FULL"
    assert db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers()).json()["duplicate"] is True


def test_evidence_api_access_restrictions(api):
    assert api.get(EV).status_code == 401
    assert api.get(EV, headers={**AUTH, "origin": "https://evil.example"}).status_code == 403
    assert api.get(EV, headers={**AUTH, "host": "evil.example"}).status_code == 403


def test_oversized_json_body_rejected(db_api, test_settings):
    capture_id = upload(db_api)
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"evidence_json_body_limit": 50})
    response = db_api.patch(f"{EV}/{capture_id}", json={"text": "x" * 200}, headers=AUTH)
    assert response.status_code == 413
