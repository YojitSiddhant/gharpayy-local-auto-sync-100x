"""Read-only local OCR/ML analytics and cleanup preview."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.ocr import OcrResult, OcrRowObservation, OcrTextCorrection, ScreenshotCapture

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_local_api_access)])


def _labels(root: Path) -> list[dict]:
    path = root / "training" / "datasets" / "synthetic" / "labels.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


@router.get("")
def analytics(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    total = int(db.scalar(select(func.count()).select_from(ScreenshotCapture)) or 0)
    duplicates = int(db.scalar(select(func.coalesce(func.sum(ScreenshotCapture.duplicate_hits), 0))) or 0)
    rows = int(db.scalar(select(func.count()).select_from(OcrRowObservation)) or 0)
    unverified = int(db.scalar(select(func.count()).select_from(OcrRowObservation).where(OcrRowObservation.review_status == "needs_review")) or 0)
    corrected = int(db.scalar(select(func.count()).select_from(OcrTextCorrection)) or 0)
    completed = int(db.scalar(select(func.count()).select_from(OcrResult).where(OcrResult.status == "completed")) or 0)
    failed = int(db.scalar(select(func.count()).select_from(OcrResult).where(OcrResult.status == "failed")) or 0)
    duration = db.scalar(select(func.avg(OcrResult.duration_ms)).where(OcrResult.status == "completed"))
    labels = _labels(Path(__file__).resolve().parents[2])
    synthetic = sum(1 for item in labels if item.get("synthetic") is True)
    approved_real = sum(1 for item in labels if item.get("synthetic") is False and item.get("review", {}).get("approved_for_training") is True)
    return {
        "capture": {"total": total, "duplicates_skipped": duplicates, "rows": rows, "processing_ms_average": round(float(duration)) if duration is not None else None},
        "ocr": {"completed": completed, "failed": failed, "unverified_rows": unverified, "corrected_rows": corrected, "phone_accuracy": None, "message_accuracy": None, "timestamp_accuracy": None, "row_detection": {"precision": None, "recall": None}},
        "ml": {"mode": settings.ml_mode.upper(), "synthetic_examples": synthetic, "approved_real_examples": approved_real, "held_out_examples": 0, "current_model": None, "last_training_at": None, "last_evaluation_at": None, "benchmarks": [], "accuracy_note": "Not measured against approved real-world ground truth."},
        "readiness": ["Approved real labeled samples", "Independent evaluation examples", "Sufficient class coverage", "Benchmark results"],
    }


@router.get("/cleanup-preview")
def cleanup_preview(db: Session = Depends(get_db)):
    captures = list(db.scalars(select(ScreenshotCapture).order_by(ScreenshotCapture.received_at.desc())))
    labels = _labels(Path(__file__).resolve().parents[2])
    linked_hashes = {item.get("screenshot_sha256") for item in labels}
    return {"deletion_requires_confirmation": True, "purge_executed": False, "scope": [{"id": c.id, "label": c.screenshot_label or c.original_filename or c.id, "status": c.status, "review_state": c.review_state, "trashed": c.trashed_at is not None, "ocr_observations": int(db.scalar(select(func.count()).select_from(OcrRowObservation).join(OcrResult, OcrRowObservation.ocr_result_id == OcrResult.id).where(OcrResult.capture_id == c.id)) or 0), "corrections": int(db.scalar(select(func.count()).select_from(OcrTextCorrection).where(OcrTextCorrection.capture_id == c.id)) or 0), "linked_training_example": c.sha256 in linked_hashes} for c in captures]}
