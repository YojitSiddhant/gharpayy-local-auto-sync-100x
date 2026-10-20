from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.core.config import Settings, get_settings
from backend.app.main import app
from backend.app.models import OcrResult, OcrRowObservation, ScreenshotCapture
from backend.app.ocr.engine import OcrTimeoutError, OcrUnavailableError
from backend.tests.conftest import TEST_TOKEN, chat_list_image, image_bytes, upload_headers

URL = "/api/v1/ocr/extract"


def code(response) -> str:
    return response.json()["error"]["code"]


# --- access restrictions -------------------------------------------------------------

def test_requires_token(api):
    headers = upload_headers()
    del headers["x-local-api-token"]
    response = api.post(URL, content=image_bytes(), headers=headers)
    assert response.status_code == 401 and code(response) == "LOCAL_TOKEN_REQUIRED"


def test_rejects_wrong_token(api):
    response = api.post(URL, content=image_bytes(), headers=upload_headers(**{"x-local-api-token": "y" * 60}))
    assert response.status_code == 401


def test_fails_closed_when_token_not_configured(api, test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"local_api_token": ""})
    response = api.post(URL, content=image_bytes(), headers=upload_headers())
    assert response.status_code == 503 and code(response) == "LOCAL_TOKEN_NOT_CONFIGURED"


def test_rejects_foreign_origin_even_with_token(api):
    response = api.post(URL, content=image_bytes(), headers=upload_headers(origin="https://evil.example"))
    assert response.status_code == 403 and code(response) == "ORIGIN_NOT_ALLOWED"


def test_rejects_non_local_host_header(api):
    response = api.post(URL, content=image_bytes(), headers=upload_headers(host="attacker.example"))
    assert response.status_code == 403 and code(response) == "HOST_NOT_ALLOWED"


def test_rejects_non_loopback_client_outside_test_mode(test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"app_env": "development"})
    try:
        response = TestClient(app).post(URL, content=image_bytes(), headers=upload_headers())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403 and code(response) == "LOOPBACK_ONLY"


def test_cors_does_not_allow_post_preflight(api):
    response = api.options(URL, headers={"origin": "http://localhost:3000", "access-control-request-method": "POST"})
    assert "POST" not in response.headers.get("access-control-allow-methods", "")


def test_health_stays_public():
    assert TestClient(app).get("/health").status_code == 200


# --- validation (no database needed) ------------------------------------------------

def test_invalid_file_type(api):
    response = api.post(URL, content=b"GIF89a....", headers=upload_headers("image/gif"))
    assert response.status_code == 415 and code(response) == "UNSUPPORTED_MEDIA_TYPE"


def test_incorrect_signature(api):
    response = api.post(URL, content=b"this is text, not a png", headers=upload_headers())
    assert response.status_code == 415 and code(response) == "INVALID_SIGNATURE"


def test_corrupted_image(api):
    data = image_bytes("PNG", (400, 400), "red")
    response = api.post(URL, content=data[:60], headers=upload_headers())
    assert response.status_code == 422 and code(response) == "CORRUPTED_IMAGE"


def test_empty_file(api):
    response = api.post(URL, content=b"", headers=upload_headers())
    assert response.status_code == 400 and code(response) == "EMPTY_UPLOAD"


def test_oversized_upload(api, test_settings):
    app.dependency_overrides[get_settings] = lambda: test_settings.model_copy(update={"ocr_max_upload_bytes": 1024})
    response = api.post(URL, content=image_bytes("PNG", (800, 800), "red") + b"\x00" * 2048, headers=upload_headers())
    assert response.status_code == 413 and code(response) == "UPLOAD_TOO_LARGE"


def test_excessive_dimensions(api):
    response = api.post(URL, content=image_bytes("PNG", (10_001, 40)), headers=upload_headers())
    assert response.status_code == 413 and code(response) == "IMAGE_DIMENSIONS_TOO_LARGE"


def test_account_label_required(api):
    headers = upload_headers()
    del headers["x-account-label"]
    response = api.post(URL, content=image_bytes(), headers=headers)
    assert response.status_code == 400 and code(response) == "ACCOUNT_LABEL_REQUIRED"


# --- full pipeline with MySQL persistence (rolled back) ----------------------------

def test_valid_upload_persists_review_only_evidence(db_api, mysql_session, test_settings, fake_engine):
    data = chat_list_image()
    response = db_api.post(URL, content=data, headers=upload_headers(**{"x-filename": "synthetic%20chat.png"}))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert body["capture"]["review_status"] == "unverified_review_only"
    assert body["capture"]["original_filename"] == "synthetic chat.png"
    assert body["result"]["engine"] == {"name": "tesseract", "version": "5.5.3", "language": "eng", "psm": 11}
    assert body["rows"][0]["phone"] == {"raw": "+91 98765 43210", "normalized": "+919876543210", "status": "candidate"}
    assert body["rows"][1]["phone"]["normalized"] is None
    assert any("CRM import is disabled" in n for n in body["notices"])
    assert response.headers["cache-control"] == "no-store"

    capture = mysql_session.get(ScreenshotCapture, body["capture"]["id"])
    assert capture.status == "completed" and capture.sha256 == body["capture"]["sha256"]
    assert mysql_session.scalar(select(func.count()).select_from(OcrRowObservation).join(OcrResult)
                                .where(OcrResult.capture_id == capture.id)) == 2
    stored = Path(test_settings.screenshot_storage_dir) / capture.evidence_storage_key
    assert stored.read_bytes() == data
    assert stored.stat().st_mode & 0o777 == 0o600


def test_duplicate_upload_reuses_result_without_new_ocr(db_api, mysql_session, fake_engine):
    data = chat_list_image()
    first = db_api.post(URL, content=data, headers=upload_headers())
    second = db_api.post(URL, content=data, headers=upload_headers())
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["duplicate"] is True
    assert second.json()["capture"]["id"] == first.json()["capture"]["id"]
    assert fake_engine.calls == 1
    assert mysql_session.scalar(select(func.count()).select_from(ScreenshotCapture)
                                .where(ScreenshotCapture.sha256 == first.json()["capture"]["sha256"])) == 1


def test_same_image_under_other_account_is_held_separately(db_api, fake_engine):
    data = chat_list_image()
    first = db_api.post(URL, content=data, headers=upload_headers(account="Account A"))
    second = db_api.post(URL, content=data, headers=upload_headers(account="Account B"))
    assert second.status_code == 201
    assert second.json()["capture"]["id"] != first.json()["capture"]["id"]
    assert second.json()["capture"]["cross_account_reuse"] is True
    assert fake_engine.calls == 2


def test_ocr_timeout_is_recorded_and_returns_504(db_api, mysql_session, fake_engine):
    fake_engine.error = OcrTimeoutError("slow")
    response = db_api.post(URL, content=chat_list_image(), headers=upload_headers())
    assert response.status_code == 504 and code(response) == "OCR_TIMEOUT"
    capture = mysql_session.get(ScreenshotCapture, response.json()["error"]["details"]["capture_id"])
    assert capture.status == "failed" and capture.error_code == "OCR_TIMEOUT"
    assert capture.results[0].status == "failed"


def test_failed_capture_is_retried_on_reupload(db_api, mysql_session, fake_engine):
    fake_engine.error = OcrUnavailableError("missing")
    failed = db_api.post(URL, content=chat_list_image(), headers=upload_headers())
    assert failed.status_code == 503
    fake_engine.error = None
    retried = db_api.post(URL, content=chat_list_image(), headers=upload_headers())
    assert retried.status_code == 201 and retried.json()["result"]["attempt"] == 2
    assert retried.json()["capture"]["id"] == failed.json()["error"]["details"]["capture_id"]


def test_token_value_never_echoed(db_api):
    response = db_api.post(URL, content=chat_list_image(), headers=upload_headers())
    assert TEST_TOKEN not in response.text


def test_settings_default_storage_is_private():
    default = Settings(_env_file=None).screenshot_storage_dir
    assert "public" not in default and default.startswith("backend/.private_storage")
