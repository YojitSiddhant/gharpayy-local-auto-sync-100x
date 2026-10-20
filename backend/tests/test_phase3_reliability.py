"""Phase 3 reliability: merged OCR lines, failure recording, stale recovery, duplicates, retention."""
import shutil
from datetime import timedelta

import pytest
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from backend.app.models import EvidenceAuditEvent, ScreenshotCapture
from backend.app.models.ocr import utcnow
from backend.app.ocr import retention
from backend.app.ocr.engine import OcrLine, OcrWord, TesseractEngine
from backend.app.ocr.service import recover_stale_processing
from backend.app.ocr.whatsapp_parser import parse_chat_list, split_trailing_timestamps
from backend.tests.conftest import TEST_TOKEN, chat_list_image, make_line, upload_headers

W, H = 720, 1280
UPLOAD = "/api/v1/ocr/extract"
EV = "/api/v1/evidence"
AUTH = {"x-local-api-token": TEST_TOKEN}


def words_line(parts: list[tuple[str, int]], top: int, confidence: float = 90.0) -> OcrLine:
    """An OCR line built from (word, left) pairs, as Tesseract reports them."""
    words = [OcrWord(text=t, confidence=confidence, left=x, top=top, right=x + 14 * len(t), bottom=top + 28) for t, x in parts]
    return OcrLine(text=" ".join(t for t, _ in parts), confidence=confidence, left=words[0].left, top=top,
                   right=words[-1].right, bottom=top + 28, words=words)


# --- merged header + timestamp lines -------------------------------------------------

def test_merged_phone_header_and_time_are_split_from_word_boxes():
    lines = [words_line([("+91", 140), ("98765", 200), ("43210", 290), ("2:52pm", 400)], 180), make_line("Need a room", 140, 228)]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.phone_normalized == "+919876543210" and row.visible_timestamp_text == "2:52pm"
    assert row.message_preview == "Need a room"
    assert any("separated from a merged OCR header" in w for w in row.warnings)


def test_two_word_timestamp_and_relative_word_split():
    split = split_trailing_timestamps([words_line([("+91", 140), ("94491", 200), ("90529", 290), ("Yesterday", 380)], 400),
                                       words_line([("Alex", 140), ("10:24", 260), ("am", 330)], 600)])
    assert [s.text for s in split] == ["+91 94491 90529", "Yesterday", "Alex", "10:24 am"]
    assert split[1].head_is_phone and not split[3].head_is_phone


def test_time_inside_a_preview_never_creates_a_phantom_row():
    lines = [make_line("+91 98765 43210", 140, 180), make_line("3:00 pm", 560, 184),
             words_line([("see", 140), ("you", 200), ("at", 260), ("5:00", 300), ("pm", 370)], 228)]
    rows = parse_chat_list(lines, W, H).rows
    assert len(rows) == 1 and rows[0].message_preview == "see you at 5:00 pm"


def test_lines_without_word_geometry_are_not_split():
    line = OcrLine(text="+91 98765 43210 2:52 pm", confidence=90, left=140, top=180, right=500, bottom=208, words=[])
    assert split_trailing_timestamps([line]) == [line]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract executable not installed")
def test_real_tesseract_tight_header_timestamp_spacing_recovers_rows():
    """Synthetic benchmark (3 rows, tight spacing). Before Phase 3 only 1 of 3 rows was found."""
    image = Image.new("RGB", (480, 900), "white")
    draw = ImageDraw.Draw(image)
    big, small = ImageFont.load_default(size=22), ImageFont.load_default(size=18)
    y = 120
    for header, stamp, preview in [("+91 98765 43210", "2:52 pm", "Need a room"), ("Akhil Kumar Sharma", "1:15 pm", "Thanks"), ("+91 94491 90529", "Yesterday", "ok")]:
        draw.text((70, y), header, fill="black", font=big)
        draw.text((70 + draw.textlength(header, font=big) + 18, y + 2), stamp, fill="#555", font=small)
        draw.text((70, y + 34), preview, fill="#444", font=small)
        y += 110
    output = TesseractEngine(30, 11).run(image)
    rows = parse_chat_list(output.lines, image.width, image.height).rows
    assert len(rows) == 3
    assert {r.phone_normalized for r in rows if r.phone_normalized} == {"+919876543210", "+919449190529"}
    assert all(r.visible_timestamp_text for r in rows)


# --- failure recording and stale recovery --------------------------------------------

def test_unexpected_engine_crash_is_recorded_as_retryable_failure(db_api, fake_engine):
    fake_engine.error = ValueError("unexpected bug")
    response = db_api.post(UPLOAD, content=chat_list_image(), headers=upload_headers())
    assert response.status_code == 500 and response.json()["error"]["code"] == "OCR_FAILED"
    capture_id = response.json()["error"]["details"]["capture_id"]
    assert db_api.get(f"{EV}/{capture_id}", headers=AUTH).json()["status"] == "error"
    fake_engine.error = None
    assert db_api.post(f"{EV}/{capture_id}/retry", json={}, headers=AUTH).json()["status"] == "review"


def test_stale_processing_capture_becomes_retryable(db_api, mysql_session, test_settings):
    stuck = ScreenshotCapture(sha256="b" * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=1,
                              width=100, height=100, status="processing", updated_at=utcnow() - timedelta(minutes=10))
    fresh = ScreenshotCapture(sha256="c" * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=1,
                              width=100, height=100, status="processing")
    mysql_session.add_all([stuck, fresh])
    mysql_session.commit()
    statuses = {i["id"]: i["status"] for i in db_api.get(EV, headers=AUTH).json()["items"]}
    assert statuses[stuck.id] == "error" and statuses[fresh.id] == "processing"
    mysql_session.refresh(stuck)
    assert stuck.error_code == "OCR_INTERRUPTED"
    assert recover_stale_processing(mysql_session, test_settings) == 0  # idempotent


# --- duplicates ------------------------------------------------------------------------

def test_duplicate_counter_increments_atomically(db_api, mysql_session):
    data = chat_list_image()
    for _ in range(3):
        db_api.post(UPLOAD, content=data, headers=upload_headers())
    capture = mysql_session.scalar(select(ScreenshotCapture))
    mysql_session.refresh(capture)
    assert capture.duplicate_hits == 2


def test_duplicate_of_trashed_evidence_is_flagged(db_api):
    data = chat_list_image()
    capture_id = db_api.post(UPLOAD, content=data, headers=upload_headers()).json()["capture"]["id"]
    db_api.post(f"{EV}/{capture_id}/trash", json={"reason": "wrong"}, headers=AUTH)
    again = db_api.post(UPLOAD, content=data, headers=upload_headers()).json()
    assert again["duplicate"] is True and again["capture"]["trashed"] is True


# --- retention ---------------------------------------------------------------------------

def _aged_capture(session, sha: str, days_old: int, **extra) -> ScreenshotCapture:
    capture = ScreenshotCapture(sha256=sha * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=1,
                                width=100, height=100, status="completed", captured_at=utcnow() - timedelta(days=days_old), **extra)
    session.add(capture)
    session.commit()
    return capture


def test_retention_is_disabled_by_default_and_dry_run_changes_nothing(mysql_session):
    old = _aged_capture(mysql_session, "d", 40)
    assert retention.retention_candidates(mysql_session, 0) == []
    assert retention.plan(mysql_session, 30) == {"total": 1, "review": 1}
    mysql_session.refresh(old)
    assert old.trashed_at is None


def test_retention_apply_only_trashes_old_evidence_reversibly(mysql_session, db_api):
    old = _aged_capture(mysql_session, "e", 40)
    recent = _aged_capture(mysql_session, "f", 2)
    already = _aged_capture(mysql_session, "1", 50, trashed_at=utcnow(), trash_reason="manual", trashed_by="R")
    assert retention.apply(mysql_session, 30) == 1
    for c in (old, recent, already):
        mysql_session.refresh(c)
    assert old.trashed_at is not None and old.trash_reason.startswith("Retention:") and old.trashed_by == "Retention policy"
    assert recent.trashed_at is None and already.trash_reason == "manual"
    events = mysql_session.scalars(select(EvidenceAuditEvent).where(EvidenceAuditEvent.capture_id == old.id)).all()
    assert [e.action for e in events] == ["trash"]
    # Still present and restorable; nothing was purged.
    restored = db_api.post(f"{EV}/{old.id}/restore", json={"reason": "keep"}, headers=AUTH).json()
    assert restored["trash"] is None


def test_retention_cli_dry_run_reports_counts_only(capsys, monkeypatch, mysql_session):
    _aged_capture(mysql_session, "2", 40, original_filename="secret-customer.png")
    monkeypatch.setattr("backend.app.db.session.SessionLocal", lambda: _NoClose(mysql_session))
    assert retention.main(["--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "Dry run: 1 evidence item(s)" in out and "secret-customer" not in out
    assert retention.main(["--days", "0"]) == 0
    assert "Retention disabled" in capsys.readouterr().out


class _NoClose:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False
