"""Saved-evidence browsing and review (parity with the reference LocalAutoSyncPanel).

Reviewed ≠ verified: no operation here changes identity status, creates leads or touches a CRM.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.errors import ApiError
from backend.app.models.ocr import EvidenceAuditEvent, OcrResult, OcrRowObservation, OcrTextCorrection, ScreenshotCapture, utcnow
from backend.app.ocr.service import storage_usage
from backend.app.schemas.ocr import iso, result_out, row_out

DEFAULT_REVIEWER = "Unidentified local reviewer"
LIST_LIMIT = 500
_KEY_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp)$")
FILTERS = ("all", "review", "reviewed", "error", "trash")

# Tentative content cues: matched words only, never emotions or automatic rejection (reference contentSignals).
SIGNAL_RULES = [
    ("urgency", re.compile(r"\b(?:urgent|asap|immediately|today itself)\b", re.I)),
    ("frustration", re.compile(r"\b(?:frustrated|annoyed|not happy|waste of time)\b", re.I)),
    ("spam", re.compile(r"\b(?:lottery|claim your prize|guaranteed returns)\b", re.I)),
    ("opt-out", re.compile(r"\b(?:stop messaging|do not contact|unsubscribe|don't contact)\b", re.I)),
]


def reviewer_name(value: str | None) -> str:
    return (value or "").strip()[:120] or DEFAULT_REVIEWER


def content_signals(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    return [{"kind": kind, "evidence": m.group(0)} for kind, pattern in SIGNAL_RULES for m in pattern.finditer(text)]


def ui_status(capture: ScreenshotCapture) -> str:
    if capture.status == "failed":
        return "error"
    if capture.status == "processing":
        return "processing"
    return capture.review_state


def latest_result(capture: ScreenshotCapture) -> OcrResult | None:
    completed = [r for r in capture.results if r.status == "completed"]
    return completed[-1] if completed else (capture.results[-1] if capture.results else None)


def original_text(capture: ScreenshotCapture) -> str:
    completed = [r for r in capture.results if r.status == "completed"]
    # The earliest successful OCR is the preserved original; later retries never replace it.
    return (completed[0].raw_text or "") if completed else ""


def current_text(capture: ScreenshotCapture) -> str:
    return capture.corrected_text if capture.corrected_text is not None else original_text(capture)


def get_capture(db: Session, capture_id: str) -> ScreenshotCapture:
    capture = db.get(ScreenshotCapture, capture_id)
    if capture is None:
        raise ApiError(404, "EVIDENCE_NOT_FOUND", "Saved evidence not found.")
    return capture


def _ordered():
    return func.coalesce(ScreenshotCapture.captured_at, ScreenshotCapture.received_at).desc()


def list_evidence(db: Session, search: str = "", status_filter: str = "all") -> list[ScreenshotCapture]:
    if status_filter not in FILTERS:
        raise ApiError(400, "INVALID_FILTER", "Unknown review status filter.")
    query = select(ScreenshotCapture)
    trashed = ScreenshotCapture.trashed_at.is_not(None)
    conditions = {
        "all": ~trashed,
        "review": and_(~trashed, ScreenshotCapture.status == "completed", ScreenshotCapture.review_state == "review"),
        "reviewed": and_(~trashed, ScreenshotCapture.status == "completed", ScreenshotCapture.review_state == "reviewed"),
        "error": and_(~trashed, ScreenshotCapture.status == "failed"),
        "trash": trashed,
    }
    query = query.where(conditions[status_filter])
    term = search.strip()[:200]
    if term:
        like = "%" + term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        digits = re.sub(r"\D", "", term)
        text_match = [
            func.lower(ScreenshotCapture.original_filename).like(like, escape="\\"),
            func.lower(ScreenshotCapture.screenshot_label).like(like, escape="\\"),
            func.lower(ScreenshotCapture.account_label).like(like, escape="\\"),
            func.lower(ScreenshotCapture.corrected_text).like(like, escape="\\"),
            exists().where(OcrResult.capture_id == ScreenshotCapture.id, func.lower(OcrResult.raw_text).like(like, escape="\\")),
        ]
        if len(digits) >= 3:
            text_match.append(exists().where(
                OcrResult.capture_id == ScreenshotCapture.id, OcrRowObservation.ocr_result_id == OcrResult.id,
                OcrRowObservation.phone_candidate_normalized.like(f"%{digits}%")))
        query = query.where(or_(*text_match))
    return list(db.scalars(query.order_by(_ordered()).limit(LIST_LIMIT)))


def evidence_stats(db: Session, settings: Settings) -> dict[str, Any]:
    active = ScreenshotCapture.trashed_at.is_(None)

    def count(*conditions) -> int:
        return int(db.scalar(select(func.count()).select_from(ScreenshotCapture).where(*conditions)) or 0)

    average = db.scalar(select(func.avg(OcrResult.duration_ms)).join(ScreenshotCapture, OcrResult.capture_id == ScreenshotCapture.id)
                        .where(active, OcrResult.status == "completed"))
    records, stored = storage_usage(db)
    return {
        "review": count(active, ScreenshotCapture.status == "completed", ScreenshotCapture.review_state == "review"),
        "reviewed": count(active, ScreenshotCapture.status == "completed", ScreenshotCapture.review_state == "reviewed"),
        "failed": count(active, ScreenshotCapture.status == "failed"),
        "trash": count(ScreenshotCapture.trashed_at.is_not(None)),
        "average_ms": round(float(average)) if average is not None else 0,
        "records": records,
        "max_records": settings.evidence_max_records,
        "stored_bytes": stored,
        "max_bytes": settings.evidence_max_bytes,
        "duplicates": int(db.scalar(select(func.coalesce(func.sum(ScreenshotCapture.duplicate_hits), 0))) or 0),
    }


def association_conflicts(db: Session, capture: ScreenshotCapture) -> list[str]:
    """Parser quarantines plus reviewer names that map to different phones across active evidence."""
    result = latest_result(capture)
    rows = result.rows if result else []
    conflicts = [f"Row {r.position}: {_quarantine_reason(r)}" for r in rows if r.quarantined]
    for row in rows:
        name = (row.observed_name or "").strip()
        if not name:
            continue
        phones = set(db.scalars(
            select(OcrRowObservation.phone_candidate_normalized)
            .join(OcrResult, OcrRowObservation.ocr_result_id == OcrResult.id)
            .join(ScreenshotCapture, OcrResult.capture_id == ScreenshotCapture.id)
            .where(ScreenshotCapture.trashed_at.is_(None), func.lower(OcrRowObservation.observed_name) == name.lower())
        ))
        if len(phones) > 1:
            conflicts.append(f"Name “{name}” has different phone numbers — association quarantined")
    return conflicts


def _quarantine_reason(row: OcrRowObservation) -> str:
    for warning in row.warnings or []:
        if re.search(r"quarantin|merge|not normalized|confusable|malformed|unsupported|country code", warning, re.I):
            return warning
    return "Unresolved identity"


def serialize_summary(capture: ScreenshotCapture) -> dict[str, Any]:
    return {
        "id": capture.id, "label": capture.screenshot_label or capture.original_filename or capture.id,
        "file_name": capture.original_filename, "status": ui_status(capture), "trashed": capture.trashed_at is not None,
        "account_label": capture.account_label, "captured_at": iso(capture.captured_at or capture.received_at),
    }


def _audit_trail(db: Session, capture_id: str) -> list[EvidenceAuditEvent]:
    return list(db.scalars(select(EvidenceAuditEvent).where(EvidenceAuditEvent.capture_id == capture_id).order_by(EvidenceAuditEvent.created_at)))


def serialize_detail(db: Session, capture: ScreenshotCapture) -> dict[str, Any]:
    result = latest_result(capture)
    trail = _audit_trail(db, capture.id)
    conflicts = association_conflicts(db, capture)
    return {
        **serialize_summary(capture),
        "sha256": capture.sha256, "mime_type": capture.mime_type, "byte_size": capture.byte_size,
        "dimensions": {"width": capture.width, "height": capture.height},
        "original_dimensions": {"width": capture.original_width, "height": capture.original_height} if capture.original_width else None,
        "crop": capture.crop, "source_mode": capture.source_mode, "source_label": capture.source_label,
        "screenshot_label": capture.screenshot_label, "received_at": iso(capture.received_at),
        "revision": capture.revision,
        "review_status": capture.review_status, "identity_status": "unverified",
        "reviewed_at": iso(capture.reviewed_at), "reviewed_by": capture.reviewed_by,
        "cross_account_reuse": capture.cross_account_reuse, "duplicate_hits": capture.duplicate_hits,
        "evidence_stored": capture.evidence_storage_key is not None,
        "error": {"code": capture.error_code, "message": capture.error_message} if capture.error_code else None,
        "text": current_text(capture), "original_text": original_text(capture),
        "corrections": [{"time": iso(c.created_at), "old_text": c.old_text, "new_text": c.new_text, "reviewer": c.reviewer} for c in capture.corrections],
        "trash": {"time": iso(capture.trashed_at), "reason": capture.trash_reason, "reviewer": capture.trashed_by} if capture.trashed_at else None,
        "trash_history": [{"action": e.action, "time": iso(e.created_at), "reason": e.reason, "reviewer": e.reviewer} for e in trail if e.action in ("trash", "restore")],
        "result": result_out(result).model_dump(mode="json") if result else None,
        "rows": [row_out(r).model_dump(mode="json") for r in (result.rows if result else [])],
        "conflicts": conflicts, "quarantined": bool(conflicts),
        "signals": content_signals(original_text(capture)),
        "proposals": list(capture.region_proposals or []),
    }


def export_evidence(db: Session, include_trash: bool = False) -> list[dict[str, Any]]:
    query = select(ScreenshotCapture).order_by(_ordered())
    if not include_trash:
        query = query.where(ScreenshotCapture.trashed_at.is_(None))
    out = []
    for capture in db.scalars(query):
        detail = serialize_detail(db, capture)  # contains no image bytes or file paths
        detail["association_review"] = {"quarantined": detail["quarantined"], "conflicts": detail["conflicts"], "identity_status": "unverified"}
        out.append(detail)
    return out


def evidence_path(settings: Settings, capture: ScreenshotCapture) -> Path:
    key = capture.evidence_storage_key
    if not key or not _KEY_RE.fullmatch(key):
        raise ApiError(404, "EVIDENCE_IMAGE_NOT_RETAINED", "The screenshot image was not retained.")
    root = Path(settings.screenshot_storage_dir).resolve()
    path = (root / key).resolve()
    if path.parent != root or (root / key).is_symlink() or not path.is_file():
        raise ApiError(404, "EVIDENCE_IMAGE_NOT_RETAINED", "The screenshot image was not retained.")
    return path


def _audit(db: Session, capture: ScreenshotCapture, action: str, reviewer: str, reason: str | None = None) -> None:
    db.add(EvidenceAuditEvent(capture_id=capture.id, capture_sha256=capture.sha256, action=action, reason=reason, reviewer=reviewer))


CONFLICT_MESSAGE = "This reading was changed in another tab or by another reviewer. Your change was not saved."


def _fail(db: Session, error: ApiError) -> None:
    db.rollback()  # undo the revision claim; nothing partial is ever committed
    raise error


def claim_revision(db: Session, capture: ScreenshotCapture, expected: int | None) -> None:
    """Atomically take the next revision of this evidence (optimistic concurrency).

    UPDATE ... SET revision = revision + 1 WHERE id = ? [AND revision = expected]. InnoDB serialises concurrent
    writers on the row lock, so of two edits based on the same revision exactly one matches; the other gets a
    409 instead of silently overwriting. The capture is then re-read from the locked, latest row version.
    """
    stmt = update(ScreenshotCapture).where(ScreenshotCapture.id == capture.id)
    if expected is not None:
        stmt = stmt.where(ScreenshotCapture.revision == expected)
    if db.execute(stmt.values(revision=ScreenshotCapture.revision + 1)).rowcount == 0:
        db.rollback()
        current = db.scalar(select(ScreenshotCapture.revision).where(ScreenshotCapture.id == capture.id))
        if current is None:
            raise ApiError(404, "EVIDENCE_NOT_FOUND", "Saved evidence not found.")
        raise ApiError(409, "EDIT_CONFLICT", CONFLICT_MESSAGE, {"current_revision": current, "expected_revision": expected})
    db.refresh(capture)


def update_evidence(db: Session, capture: ScreenshotCapture, *, reviewer: str | None, screenshot_label: str | None,
                    text: str | None, observed_names: list[tuple[int, str]] | None, expected_revision: int) -> None:
    who = reviewer_name(reviewer)
    claim_revision(db, capture, expected_revision)
    if screenshot_label is not None:
        capture.screenshot_label = screenshot_label.strip()[:255] or None
    if text is not None:
        if capture.trashed_at is not None:
            _fail(db, ApiError(409, "EVIDENCE_TRASHED", "Restore this evidence before correcting it."))
        if capture.status != "completed":
            _fail(db, ApiError(409, "EVIDENCE_NOT_READY", "Only completed OCR readings can be corrected."))
        old = current_text(capture)
        if text != old:
            # Correction trail, corrected text and review state change in one transaction with the revision claim.
            capture.corrections.append(OcrTextCorrection(old_text=old, new_text=text, reviewer=who))
            capture.corrected_text = text
            capture.review_state = "review"  # a correction re-opens review; identity stays unverified
    if observed_names:
        result = latest_result(capture)
        rows = {r.position: r for r in (result.rows if result else [])}
        for position, name in observed_names:
            if position not in rows:
                _fail(db, ApiError(400, "INVALID_ROW", "Unknown header candidate position."))
            rows[position].observed_name = name.strip()[:255] or None
    db.commit()


def mark_reviewed(db: Session, capture: ScreenshotCapture, reviewer: str | None, expected_revision: int) -> None:
    # The revision proves the reviewer saw the latest text; reviewing a stale view is a conflict.
    claim_revision(db, capture, expected_revision)
    if capture.trashed_at is not None or capture.status != "completed" or capture.review_state != "review":
        _fail(db, ApiError(409, "NOT_REVIEWABLE", "Only active readings awaiting review can be marked reviewed."))
    who = reviewer_name(reviewer)
    capture.review_state, capture.reviewed_at, capture.reviewed_by = "reviewed", utcnow(), who
    _audit(db, capture, "review", who)
    db.commit()


def set_trash(db: Session, capture: ScreenshotCapture, reason: str, reviewer: str | None, restore: bool,
              expected_revision: int | None = None) -> None:
    reason = reason.strip()
    if not reason:
        raise ApiError(400, "REASON_REQUIRED", "A reason is required.")
    who = reviewer_name(reviewer)
    claim_revision(db, capture, expected_revision)
    if restore:
        if capture.trashed_at is None:
            _fail(db, ApiError(409, "NOT_TRASHED", "This evidence is not in the trash."))
        capture.trashed_at = capture.trash_reason = capture.trashed_by = None
    else:
        if capture.trashed_at is not None:
            _fail(db, ApiError(409, "ALREADY_TRASHED", "This evidence is already in the trash."))
        capture.trashed_at, capture.trash_reason, capture.trashed_by = utcnow(), reason[:500], who
    # State change and its audit event commit together.
    _audit(db, capture, "restore" if restore else "trash", who, reason[:500])
    db.commit()


def purge_trash(db: Session, settings: Settings, reviewer: str | None) -> int:
    who = reviewer_name(reviewer)
    trashed = list(db.scalars(select(ScreenshotCapture).where(ScreenshotCapture.trashed_at.is_not(None))))
    keys = {c.evidence_storage_key for c in trashed if c.evidence_storage_key}
    for capture in trashed:
        _audit(db, capture, "purge", who, "Explicit permanent purge of trashed evidence")
        db.delete(capture)
    db.commit()
    root = Path(settings.screenshot_storage_dir).resolve()
    for key in keys:
        still_used = db.scalar(select(ScreenshotCapture.id).where(ScreenshotCapture.evidence_storage_key == key).limit(1))
        if still_used is None and _KEY_RE.fullmatch(key):
            (root / key).unlink(missing_ok=True)
    return len(trashed)


def add_region_proposals(db: Session, settings: Settings, capture: ScreenshotCapture, kind: str, region: dict[str, int]) -> int:
    """Operator-selected region in OCR image pixels. Color is measured, never given a business meaning."""
    if capture.trashed_at is not None:
        raise ApiError(409, "EVIDENCE_TRASHED", "Restore this evidence first.")
    x0, y0, x1, y1 = region["x0"], region["y0"], region["x1"], region["y1"]
    if not (0 <= x0 < x1 <= capture.width and 0 <= y0 < y1 <= capture.height):
        raise ApiError(400, "INVALID_REGION", "Select a valid image region in OCR image coordinates.")
    result = latest_result(capture)
    words = [w for line in (result.lines if result else []) for w in line.get("words", [])]
    inside = [w for w in words if w["bounds"]["left"] >= x0 and w["bounds"]["top"] >= y0 and w["bounds"]["right"] <= x1 and w["bounds"]["bottom"] <= y1]
    pixels = None
    try:
        with Image.open(io.BytesIO(evidence_path(settings, capture).read_bytes())) as image:
            pixels = image.convert("RGB")
    except ApiError:
        pixels = None
    new_proposals = []
    for word in inside:
        b = word["bounds"]
        color = None
        if pixels is not None:
            px = max(0, min(pixels.width - 1, b["left"] - 2))
            py = max(0, min(pixels.height - 1, (b["top"] + b["bottom"]) // 2))
            r, g, bl = pixels.getpixel((px, py))
            color = f"rgb({r}, {g}, {bl})"
        text = str(word["text"])
        new_proposals.append({
            "kind": kind, "text": text, "bounds": {"x0": b["left"], "y0": b["top"], "x1": b["right"], "y1": b["bottom"]},
            "color": color, "color_meaning": "unknown",
            "count": int(text.strip()) if kind == "unread" and text.strip().isdigit() else None, "status": "tentative",
        })
    claim_revision(db, capture, None)  # re-reads the latest row, so concurrent proposals are appended, not lost
    capture.region_proposals = list(capture.region_proposals or []) + new_proposals
    db.commit()
    return len(inside)
