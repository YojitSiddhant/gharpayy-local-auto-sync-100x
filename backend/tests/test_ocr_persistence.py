"""MySQL schema guarantees (run inside a rolled-back transaction)."""
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.app.models import OcrResult, OcrRowObservation, ScreenshotCapture


def capture(**overrides) -> ScreenshotCapture:
    values = dict(sha256="a" * 64, account_label="Test WhatsApp", mime_type="image/png", byte_size=10,
                  width=100, height=100, status="completed")
    values.update(overrides)
    return ScreenshotCapture(**values)


def test_duplicate_sha_within_account_is_rejected_by_mysql(mysql_session):
    mysql_session.add(capture())
    mysql_session.flush()
    mysql_session.add(capture())
    with pytest.raises(IntegrityError):
        mysql_session.flush()


def test_same_sha_in_other_account_is_allowed(mysql_session):
    mysql_session.add_all([capture(), capture(account_label="Other")])
    mysql_session.flush()


@pytest.mark.parametrize("field,value", [("review_status", "verified"), ("status", "approved")])
def test_review_only_constraints_enforced(mysql_session, field, value):
    mysql_session.add(capture(**{field: value}))
    with pytest.raises((IntegrityError, OperationalError)):
        mysql_session.flush()


def test_row_identity_cannot_be_marked_verified(mysql_session):
    record = capture()
    result = OcrResult(attempt=1, status="completed", tesseract_psm=11)
    result.rows.append(OcrRowObservation(position=1, phone_status="candidate", row_left=0, row_top=0, row_right=1,
                                         row_bottom=1, identity_status="verified"))
    record.results.append(result)
    mysql_session.add(record)
    with pytest.raises((IntegrityError, OperationalError)):
        mysql_session.flush()


def test_rows_round_trip_with_json_evidence(mysql_session):
    record = capture()
    result = OcrResult(attempt=1, status="completed", tesseract_psm=11, raw_text="+91 98765 43210\nhello",
                       lines=[{"text": "hello"}], warnings=["w"], preprocessing=["grayscale"])
    result.rows.append(OcrRowObservation(position=1, phone_status="candidate", phone_candidate_normalized="+919876543210",
                                         row_left=0, row_top=1, row_right=2, row_bottom=3, warnings=["Low OCR confidence"],
                                         source_lines=[{"text": "+91 98765 43210"}], header_bounds={"left": 1, "top": 1, "right": 2, "bottom": 2}))
    record.results.append(result)
    mysql_session.add(record)
    mysql_session.flush()
    mysql_session.expire_all()
    loaded = mysql_session.get(ScreenshotCapture, record.id)
    row = loaded.results[0].rows[0]
    assert row.identity_status == "unverified" and row.sender_direction == "unknown"
    assert row.warnings == ["Low OCR confidence"] and loaded.results[0].lines == [{"text": "hello"}]
