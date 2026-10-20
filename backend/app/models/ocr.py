"""Phase 2 OCR evidence tables. Review-only: no lead, CRM or identity-merge tables.

All timestamps are stored as UTC (naive DATETIME(6) in MySQL).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

CAPTURE_STATUSES = ("processing", "completed", "failed")
REVIEW_STATES = ("review", "reviewed")
AUDIT_ACTIONS = ("trash", "restore", "review", "retry", "purge")
RESULT_STATUSES = ("completed", "failed")
PHONE_STATUSES = ("candidate", "ambiguous", "conflict", "none")
REVIEW_ONLY = "unverified_review_only"

Timestamp = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LongText = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid.uuid4())


class ScreenshotCapture(Base):
    __tablename__ = "screenshot_captures"
    __table_args__ = (
        # Exact-duplicate scope: identical file bytes under the same account label.
        UniqueConstraint("account_label", "sha256", name="uq_screenshot_captures_account_sha256"),
        CheckConstraint("status IN ('processing','completed','failed')", name="ck_screenshot_captures_status"),
        CheckConstraint(f"review_status = '{REVIEW_ONLY}'", name="ck_screenshot_captures_review_only"),
        CheckConstraint("review_state IN ('review','reviewed')", name="ck_screenshot_captures_review_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    account_label: Mapped[str] = mapped_column(String(90))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(32))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    source_mode: Mapped[str] = mapped_column(String(32), default="manual-upload")
    status: Mapped[str] = mapped_column(String(16), default="processing")
    review_status: Mapped[str] = mapped_column(String(32), default=REVIEW_ONLY)
    cross_account_reuse: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_storage_key: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(255))
    received_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow, onupdate=utcnow)

    # Evidence review (migration 20261020_0002). Review never verifies identity.
    screenshot_label: Mapped[str | None] = mapped_column(String(255))
    captured_at: Mapped[datetime | None] = mapped_column(Timestamp)
    source_label: Mapped[str | None] = mapped_column(String(255))
    original_width: Mapped[int | None] = mapped_column(Integer)
    original_height: Mapped[int | None] = mapped_column(Integer)
    crop: Mapped[dict | None] = mapped_column(JSON)
    corrected_text: Mapped[str | None] = mapped_column(LongText)
    review_state: Mapped[str] = mapped_column(String(16), default="review", server_default="review")
    reviewed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    trashed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    trash_reason: Mapped[str | None] = mapped_column(String(500))
    trashed_by: Mapped[str | None] = mapped_column(String(120))
    region_proposals: Mapped[list | None] = mapped_column(JSON)
    duplicate_hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    results: Mapped[list[OcrResult]] = relationship(back_populates="capture", order_by="OcrResult.attempt", cascade="all, delete-orphan")
    corrections: Mapped[list[OcrTextCorrection]] = relationship(back_populates="capture", order_by="OcrTextCorrection.created_at", cascade="all, delete-orphan")


class OcrResult(Base):
    __tablename__ = "ocr_results"
    __table_args__ = (
        UniqueConstraint("capture_id", "attempt", name="uq_ocr_results_capture_attempt"),
        CheckConstraint("status IN ('completed','failed')", name="ck_ocr_results_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(ForeignKey("screenshot_captures.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))
    engine_name: Mapped[str] = mapped_column(String(32), default="tesseract")
    engine_version: Mapped[str | None] = mapped_column(String(32))
    ocr_language: Mapped[str] = mapped_column(String(16), default="eng")
    tesseract_psm: Mapped[int] = mapped_column(Integer)
    preprocessing: Mapped[list] = mapped_column(JSON, default=list)
    processing_scale: Mapped[float | None] = mapped_column(Float)
    raw_text: Mapped[str | None] = mapped_column(LongText)
    mean_confidence: Mapped[float | None] = mapped_column(Float)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    lines: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(Timestamp)

    capture: Mapped[ScreenshotCapture] = relationship(back_populates="results")
    rows: Mapped[list[OcrRowObservation]] = relationship(back_populates="result", order_by="OcrRowObservation.position", cascade="all, delete-orphan")


class OcrRowObservation(Base):
    __tablename__ = "ocr_row_observations"
    __table_args__ = (
        UniqueConstraint("ocr_result_id", "position", name="uq_ocr_row_observations_result_position"),
        CheckConstraint("phone_status IN ('candidate','ambiguous','conflict','none')", name="ck_ocr_row_observations_phone_status"),
        CheckConstraint("identity_status = 'unverified'", name="ck_ocr_row_observations_identity_unverified"),
        CheckConstraint("review_status = 'needs_review'", name="ck_ocr_row_observations_review"),
        CheckConstraint("sender_direction = 'unknown'", name="ck_ocr_row_observations_sender_unknown"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    ocr_result_id: Mapped[str] = mapped_column(ForeignKey("ocr_results.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    display_header_text: Mapped[str | None] = mapped_column(String(512))
    phone_candidate_raw: Mapped[str | None] = mapped_column(String(64))
    phone_candidate_normalized: Mapped[str | None] = mapped_column(String(20), index=True)
    phone_status: Mapped[str] = mapped_column(String(16))
    message_preview: Mapped[str | None] = mapped_column(Text)
    preview_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    visible_timestamp_text: Mapped[str | None] = mapped_column(String(64))
    timestamp_kind: Mapped[str | None] = mapped_column(String(32))
    row_left: Mapped[int] = mapped_column(Integer)
    row_top: Mapped[int] = mapped_column(Integer)
    row_right: Mapped[int] = mapped_column(Integer)
    row_bottom: Mapped[int] = mapped_column(Integer)
    header_bounds: Mapped[dict | None] = mapped_column(JSON)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_status: Mapped[str] = mapped_column(String(16), default="unverified")
    review_status: Mapped[str] = mapped_column(String(16), default="needs_review")
    sender_direction: Mapped[str] = mapped_column(String(16), default="unknown")
    source_lines: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
    # Reviewer-entered display name: a review proposal only, never an identity key.
    observed_name: Mapped[str | None] = mapped_column(String(255))

    result: Mapped[OcrResult] = relationship(back_populates="rows")


class OcrTextCorrection(Base):
    """Append-only correction trail. The original OCR text in ocr_results is never modified."""
    __tablename__ = "ocr_text_corrections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str] = mapped_column(ForeignKey("screenshot_captures.id", ondelete="CASCADE"), index=True)
    old_text: Mapped[str | None] = mapped_column(LongText)
    new_text: Mapped[str] = mapped_column(LongText)
    reviewer: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    capture: Mapped[ScreenshotCapture] = relationship(back_populates="corrections")


class EvidenceAuditEvent(Base):
    """Review/trash/purge audit. No foreign key, so purge events outlive the purged evidence.
    Holds no screenshot content or OCR text."""
    __tablename__ = "evidence_audit_events"
    __table_args__ = (
        CheckConstraint("action IN ('trash','restore','review','retry','purge')", name="ck_evidence_audit_events_action"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    capture_id: Mapped[str | None] = mapped_column(String(36), index=True)
    capture_sha256: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    reviewer: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
