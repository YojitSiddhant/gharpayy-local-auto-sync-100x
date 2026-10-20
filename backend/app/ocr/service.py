"""Screenshot → OCR → parse → MySQL persistence. Review-only; never creates leads.

Manual uploads, screen-capture frames and Retry OCR all go through run_ocr_attempt().
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.auth import CaptureSession, User
from backend.app.models.ocr import OcrResult, OcrRowObservation, ScreenshotCapture, utcnow
from backend.app.ocr.engine import OcrError, OcrTimeoutError, OcrUnavailableError, TesseractEngine
from backend.app.ocr.image_validation import ValidatedImage, decode_rgb
from backend.app.ocr.storage import store_evidence
from backend.app.ocr.whatsapp_parser import PARSER_VERSION, parse_chat_list
from backend.app.ocr.duplicate_detection import eligible_phone, phone_fingerprint
from backend.app.ml.shadow import run_shadow_prediction

EngineFactory = Callable[[Settings], TesseractEngine]

_OCR_ERROR_STATUS = {OcrTimeoutError: 504, OcrUnavailableError: 503}
_OCR_ERROR_MESSAGE = {
    "OCR_TIMEOUT": "OCR exceeded the processing time limit.",
    "OCR_ENGINE_UNAVAILABLE": "Local English Tesseract OCR is not available.",
    "OCR_FAILED": "OCR could not process this screenshot.",
    "OCR_INTERRUPTED": "OCR was interrupted before completing (for example by a server restart). Use Retry OCR.",
}


@dataclass(frozen=True)
class CaptureMeta:
    source_mode: str = "manual-file"
    captured_at: datetime | None = None
    source_label: str | None = None
    screenshot_label: str | None = None
    original_width: int | None = None
    original_height: int | None = None
    crop: dict[str, int] | None = None


def default_engine(settings: Settings) -> TesseractEngine:
    return TesseractEngine(settings.ocr_tesseract_timeout_seconds, settings.ocr_tesseract_psm)


def _stale_after(settings: Settings) -> timedelta:
    return timedelta(seconds=max(60.0, settings.ocr_tesseract_timeout_seconds * 3))


def recover_stale_processing(db: Session, settings: Settings) -> int:
    """Mark captures stuck in 'processing' past the stale window as failed so they can be retried.
    Never deletes anything; the retained screenshot and earlier attempts are untouched."""
    cutoff = utcnow() - _stale_after(settings)
    result = db.execute(
        update(ScreenshotCapture)
        .where(ScreenshotCapture.status == "processing", ScreenshotCapture.updated_at < cutoff)
        .values(status="failed", error_code="OCR_INTERRUPTED", error_message=_OCR_ERROR_MESSAGE["OCR_INTERRUPTED"], updated_at=utcnow())
    )
    db.commit()
    return int(result.rowcount or 0)


def storage_usage(db: Session) -> tuple[int, int]:
    """(records incl. trash, bytes of distinct retained screenshots)."""
    records = db.scalar(select(func.count()).select_from(ScreenshotCapture)) or 0
    distinct = select(ScreenshotCapture.sha256, func.max(ScreenshotCapture.byte_size).label("size")).group_by(ScreenshotCapture.sha256).subquery()
    stored = db.scalar(select(func.coalesce(func.sum(distinct.c.size), 0))) or 0
    return int(records), int(stored)


def _ensure_capacity(db: Session, image: ValidatedImage, settings: Settings) -> None:
    records, stored = storage_usage(db)
    already = db.scalar(select(ScreenshotCapture.id).where(ScreenshotCapture.sha256 == image.sha256).limit(1)) is not None
    if records >= settings.evidence_max_records or (not already and stored + len(image.data) > settings.evidence_max_bytes):
        raise ApiError(507, "STORAGE_FULL", "Storage full. Trash does not free storage until explicitly purged. Remaining queue is paused.")


def process_screenshot(
    db: Session, image: ValidatedImage, account_label: str, filename: str | None,
    settings: Settings, engine_factory: EngineFactory = default_engine, meta: CaptureMeta | None = None,
    *, owner: User | None = None, capture_session_id: str | None = None,
) -> tuple[ScreenshotCapture, bool]:
    """Return (capture, duplicate). Raises ApiError for OCR failures after recording them."""
    meta = meta or CaptureMeta()
    session = None
    if owner is not None and capture_session_id:
        session = db.scalar(select(CaptureSession).where(CaptureSession.id == capture_session_id, CaptureSession.user_id == owner.id).with_for_update())
        if session is None or session.status != "active" or session.expires_at <= utcnow() or not owner.is_active or not session.zone.is_active:
            raise ApiError(409, "CAPTURE_SESSION_INVALID", "The capture session is no longer active.")
    capture = db.scalar(select(ScreenshotCapture).where(
        ScreenshotCapture.account_label == account_label, ScreenshotCapture.sha256 == image.sha256))
    if capture is not None:
        if owner is not None and (capture.created_by_user_id != owner.id or capture.capture_session_id is None):
            raise ApiError(409, "DUPLICATE_UNAVAILABLE", "This screenshot already exists outside the current capture ownership.")
        if capture.status == "completed":
            # Atomic increment: safe even if OCR concurrency is raised above one.
            db.execute(update(ScreenshotCapture).where(ScreenshotCapture.id == capture.id)
                       .values(duplicate_hits=ScreenshotCapture.duplicate_hits + 1))
            db.commit()
            db.refresh(capture)
            return capture, True
        if capture.status == "processing" and utcnow() - capture.updated_at < _stale_after(settings):
            raise ApiError(409, "CAPTURE_IN_PROGRESS", "This screenshot is already being processed.", {"capture_id": capture.id})
        # Previous attempt failed or was abandoned: retry on the same capture record.
        capture.status, capture.error_code, capture.error_message = "processing", None, None
        db.commit()
    else:
        _ensure_capacity(db, image, settings)
        capture = ScreenshotCapture(
            sha256=image.sha256, account_label=account_label, original_filename=filename,
            mime_type=image.mime_type, byte_size=len(image.data), width=image.width, height=image.height,
            source_mode=meta.source_mode, status="processing", captured_at=meta.captured_at,
            source_label=meta.source_label, screenshot_label=meta.screenshot_label or filename,
            original_width=meta.original_width or image.width, original_height=meta.original_height or image.height,
            crop=meta.crop,
            zone_id=session.zone_id if session else None,
            created_by_user_id=owner.id if owner else None,
            capture_session_id=session.id if session else None,
        )
        db.add(capture)
        try:
            db.commit()
        except IntegrityError:
            # A concurrent upload of the same bytes for the same account won the insert.
            db.rollback()
            existing = db.scalar(select(ScreenshotCapture).where(
                ScreenshotCapture.account_label == account_label, ScreenshotCapture.sha256 == image.sha256))
            if existing is not None and existing.status == "completed":
                return existing, True
            raise ApiError(409, "CAPTURE_IN_PROGRESS", "This screenshot is already being processed.") from None

    capture.cross_account_reuse = db.scalar(select(ScreenshotCapture.id).where(
        ScreenshotCapture.sha256 == image.sha256, ScreenshotCapture.account_label != account_label).limit(1)) is not None
    if settings.screenshot_retention_enabled and capture.evidence_storage_key is None:
        try:
            capture.evidence_storage_key = store_evidence(settings.screenshot_storage_dir, image.sha256, image.extension, image.data)
        except OSError:
            capture.evidence_storage_key = None  # evidence retention failure does not block review-only OCR
        # Link the stored file before OCR runs, so an interruption cannot leave an unreferenced (orphaned) file
        # and a later Retry OCR can still find the screenshot.
        db.commit()
    run_ocr_attempt(db, capture, image, settings, engine_factory)
    return capture, False


def run_ocr_attempt(db: Session, capture: ScreenshotCapture, image: ValidatedImage, settings: Settings, engine_factory: EngineFactory) -> OcrResult:
    """Run one OCR attempt on a capture and persist it. Never overwrites earlier attempts or corrections."""
    attempt = len(capture.results) + 1
    result = OcrResult(capture_id=capture.id, attempt=attempt, tesseract_psm=settings.ocr_tesseract_psm, started_at=utcnow())
    try:
        output = engine_factory(settings).run(decode_rgb(image.data))
        parsed = parse_chat_list(output.lines, image.width, image.height)
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, never leave the capture "processing"
        code = exc.code if isinstance(exc, OcrError) else "OCR_FAILED"
        result.status, result.error_code, result.error_message = "failed", code, _OCR_ERROR_MESSAGE.get(code)
        result.completed_at = utcnow()
        capture.status, capture.error_code, capture.error_message = "failed", code, _OCR_ERROR_MESSAGE.get(code)
        capture.results.append(result)
        db.commit()
        raise ApiError(_OCR_ERROR_STATUS.get(type(exc), 500), code, _OCR_ERROR_MESSAGE.get(code, "OCR failed."), {"capture_id": capture.id}) from None

    if settings.ml_mode == "shadow":
        # Deliberately discard shadow output: it is diagnostic-only and cannot alter OCR.
        try:
            run_shadow_prediction(output, parsed, image.width, image.height, settings)
        except Exception:  # noqa: BLE001 - an optional experiment must never fail OCR
            pass

    result.status = "completed"
    result.engine_version = output.engine_version
    result.ocr_language = output.language
    result.preprocessing = output.preprocessing
    result.processing_scale = output.processing_scale
    result.raw_text = output.raw_text
    result.mean_confidence = output.mean_confidence
    result.word_count = output.word_count
    result.line_count = len(output.lines)
    result.lines = [line.to_dict() for line in output.lines]
    result.warnings = parsed.warnings + ([] if output.lines else ["Tesseract found no text"])
    if settings.screenshot_retention_enabled and capture.evidence_storage_key is None:
        result.warnings.append("Screenshot could not be retained on disk; Retry OCR and the image preview are unavailable.")
    if capture.cross_account_reuse:
        result.warnings.append("Identical screenshot exists under another account label; held separately, identity unverified.")
    result.parser_version = PARSER_VERSION
    result.duration_ms = output.duration_ms
    result.completed_at = utcnow()
    for row in parsed.rows:
        result.rows.append(OcrRowObservation(
            position=row.position, display_header_text=row.display_header_text,
            phone_candidate_raw=row.phone_raw[:64] if row.phone_raw else None,
            phone_candidate_normalized=row.phone_normalized, phone_status=row.phone_status,
            message_preview=row.message_preview, preview_truncated=row.preview_truncated,
            visible_timestamp_text=row.visible_timestamp_text[:64] if row.visible_timestamp_text else None,
            timestamp_kind=row.timestamp_kind,
            row_left=row.row_bounds["left"], row_top=row.row_bounds["top"],
            row_right=row.row_bounds["right"], row_bottom=row.row_bounds["bottom"],
            header_bounds=row.header_bounds, ocr_confidence=row.ocr_confidence, warnings=row.warnings,
            quarantined=row.quarantined, source_lines=row.source_lines,
            phone_fingerprint=phone_fingerprint(row.phone_normalized, settings) if eligible_phone(row.phone_normalized, row.phone_status, row.quarantined, row.display_header_text) else None,
        ))
    capture.results.append(result)
    capture.status, capture.error_code, capture.error_message = "completed", None, None
    db.commit()
    return result
