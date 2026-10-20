from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel

from backend.app.models.ocr import OcrResult, OcrRowObservation, ScreenshotCapture

REVIEW_NOTICES = [
    "Unverified OCR evidence. Review against the original screenshot before any use.",
    "No lead was created. CRM import is disabled in this phase.",
    "OCR engine confidence is not accuracy. Identity, sender direction and chat completeness are not verified.",
]


def iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class Bounds(BaseModel):
    left: int
    top: int
    right: int
    bottom: int


class PhoneCandidate(BaseModel):
    raw: str | None
    normalized: str | None
    status: Literal["candidate", "ambiguous", "conflict", "none"]


class RowObservation(BaseModel):
    position: int
    display_header_text: str | None
    phone: PhoneCandidate
    message_preview: str | None
    preview_truncated: bool
    visible_timestamp_text: str | None
    timestamp_kind: str | None
    row_bounds: Bounds
    header_bounds: Bounds | None
    ocr_confidence: float | None
    warnings: list[str]
    quarantined: bool
    identity_status: Literal["unverified"]
    review_status: Literal["needs_review"]
    sender_direction: Literal["unknown"]
    observed_name: str | None = None


class EngineInfo(BaseModel):
    name: str
    version: str | None
    language: str
    psm: int


class OcrResultOut(BaseModel):
    id: str
    attempt: int
    status: Literal["completed", "failed"]
    engine: EngineInfo
    preprocessing: list[str]
    processing_scale: float | None
    raw_text: str | None
    mean_confidence: float | None
    word_count: int
    line_count: int
    lines: list[dict[str, Any]]
    warnings: list[str]
    parser_version: str | None
    duration_ms: int | None
    started_at: str | None
    completed_at: str | None


class CaptureOut(BaseModel):
    id: str
    sha256: str
    account_label: str
    original_filename: str | None
    mime_type: str
    byte_size: int
    width: int
    height: int
    source_mode: str
    status: Literal["processing", "completed", "failed"]
    review_status: Literal["unverified_review_only"]
    cross_account_reuse: bool
    evidence_stored: bool
    trashed: bool = False
    received_at: str | None


class ExtractResponse(BaseModel):
    duplicate: bool
    capture: CaptureOut
    result: OcrResultOut | None
    rows: list[RowObservation]
    notices: list[str]


def row_out(row: OcrRowObservation) -> RowObservation:
    return RowObservation(
        position=row.position, display_header_text=row.display_header_text,
        phone=PhoneCandidate(raw=row.phone_candidate_raw, normalized=row.phone_candidate_normalized, status=row.phone_status),
        message_preview=row.message_preview, preview_truncated=row.preview_truncated,
        visible_timestamp_text=row.visible_timestamp_text, timestamp_kind=row.timestamp_kind,
        row_bounds=Bounds(left=row.row_left, top=row.row_top, right=row.row_right, bottom=row.row_bottom),
        header_bounds=Bounds(**row.header_bounds) if row.header_bounds else None,
        ocr_confidence=row.ocr_confidence, warnings=list(row.warnings or []), quarantined=row.quarantined,
        identity_status=row.identity_status, review_status=row.review_status, sender_direction=row.sender_direction,
        observed_name=row.observed_name,
    )


def result_out(result: OcrResult) -> OcrResultOut:
    return OcrResultOut(
        id=result.id, attempt=result.attempt, status=result.status,
        engine=EngineInfo(name=result.engine_name, version=result.engine_version, language=result.ocr_language, psm=result.tesseract_psm),
        preprocessing=list(result.preprocessing or []), processing_scale=result.processing_scale,
        raw_text=result.raw_text, mean_confidence=result.mean_confidence, word_count=result.word_count,
        line_count=result.line_count, lines=list(result.lines or []), warnings=list(result.warnings or []),
        parser_version=result.parser_version, duration_ms=result.duration_ms,
        started_at=iso(result.started_at), completed_at=iso(result.completed_at),
    )


def build_extract_response(capture: ScreenshotCapture, duplicate: bool) -> ExtractResponse:
    completed = [r for r in capture.results if r.status == "completed"]
    latest = completed[-1] if completed else (capture.results[-1] if capture.results else None)
    return ExtractResponse(
        duplicate=duplicate,
        capture=CaptureOut(
            id=capture.id, sha256=capture.sha256, account_label=capture.account_label,
            original_filename=capture.original_filename, mime_type=capture.mime_type, byte_size=capture.byte_size,
            width=capture.width, height=capture.height, source_mode=capture.source_mode, status=capture.status,
            review_status=capture.review_status, cross_account_reuse=capture.cross_account_reuse,
            evidence_stored=capture.evidence_storage_key is not None, trashed=capture.trashed_at is not None, received_at=iso(capture.received_at),
        ),
        result=result_out(latest) if latest else None,
        rows=[row_out(row) for row in latest.rows] if latest else [],
        notices=REVIEW_NOTICES,
    )
