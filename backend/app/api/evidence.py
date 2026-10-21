"""Protected saved-evidence API (list, detail, image, review, corrections, trash, purge, export).

Same local-only boundary as OCR upload. Responses never contain file paths; images are served
only through this authenticated route, never from a static directory.
"""
import re
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.ocr import get_engine_factory, image_limits
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.ocr import EvidenceAuditEvent, OcrFieldVerification, OcrFieldVerificationAudit
from backend.app.ocr import evidence as ev
from backend.app.ocr.concurrency import run_ocr_bounded
from backend.app.ocr.image_validation import validate_image
from backend.app.ocr.service import EngineFactory, recover_stale_processing, run_ocr_attempt


def limit_json_body(request: Request, settings: Settings = Depends(get_settings)) -> None:
    length = request.headers.get("content-length")
    if request.method in ("POST", "PATCH") and (length is None or not length.isdigit() or int(length) > settings.evidence_json_body_limit):
        raise ApiError(413, "BODY_TOO_LARGE", "Request body is missing a length or too large.")


router = APIRouter(prefix="/evidence", tags=["evidence"], dependencies=[Depends(require_local_api_access), Depends(limit_json_body)])
NO_STORE = {"Cache-Control": "no-store"}


class Reviewer(BaseModel):
    reviewer: str | None = Field(default=None, max_length=120)


class ObservedName(BaseModel):
    position: int = Field(ge=1)
    name: str = Field(max_length=255)


class EvidencePatch(Reviewer):
    # Required: the revision this edit was based on. Stale edits are rejected with 409 EDIT_CONFLICT.
    expected_revision: int = Field(ge=0)
    screenshot_label: str | None = Field(default=None, max_length=255)
    text: str | None = Field(default=None, max_length=500_000)
    observed_names: list[ObservedName] | None = Field(default=None, max_length=200)


class ReviewBody(Reviewer):
    expected_revision: int = Field(ge=0)


class ReasonBody(Reviewer):
    reason: str = Field(min_length=1, max_length=500)
    expected_revision: int | None = Field(default=None, ge=0)


class RetryBody(Reviewer):
    expected_revision: int | None = Field(default=None, ge=0)


class Region(BaseModel):
    x0: int = Field(ge=0)
    y0: int = Field(ge=0)
    x1: int = Field(ge=0)
    y1: int = Field(ge=0)


class ProposalBody(BaseModel):
    kind: Literal["label", "unread"]
    region: Region


class PurgeBody(Reviewer):
    confirm: Literal[True]

class ClearMessageBody(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)


def _safe_phone_format(original: str | None, suggested: object) -> str | None:
    if not isinstance(suggested, str) or not suggested.strip() or not original:
        return None
    value = suggested.strip()
    if re.sub(r"\D", "", value) != re.sub(r"\D", "", original):
        return None
    return value if value != original else None


def _safe_message_cleanup(original: str | None, suggested: object, uncertain: bool) -> str | None:
    if not isinstance(suggested, str) or not suggested.strip() or not original or uncertain:
        return None
    value = re.sub(r"\s+", " ", suggested.strip())
    source = re.sub(r"\s+", " ", original.strip())
    if value == original:
        return None
    if re.sub(r"[^\w\d]+", "", value, flags=re.UNICODE).casefold() == re.sub(r"[^\w\d]+", "", source, flags=re.UNICODE).casefold():
        return value
    # A short, explicitly identified OCR prefix may be removed only when the remainder is unchanged.
    cleaned = re.sub(r"^(?:vw|sy|vy)\s+", "", source, flags=re.IGNORECASE)
    if cleaned != source and value.casefold() == cleaned.casefold():
        return value
    return None


def _business_label_cleanup(row) -> tuple[str | None, bool]:
    """Remove only a geometry-proven suffix made of separate badge lines."""
    original = (row.message_preview or "").strip()
    lines = list(row.source_lines or [])
    if not original or (len(lines) < 2 and not any(item.get("business_label_words") for item in lines)):
        return None, False
    lines.sort(key=lambda item: (item.get("bounds", {}).get("top", 0), item.get("bounds", {}).get("left", 0)))
    def badge_text(item) -> str:
        text = str(item.get("text") or "").strip()
        return text if item.get("business_label_candidate") is True and text and re.fullmatch(r"[A-Z][A-Z0-9 ]{1,39}", text) and not any(ch.isdigit() for ch in text) and len(text.split()) <= 5 else ""

    candidates: list[tuple[str, dict]] = []
    for index in range(len(lines) - 1, -1, -1):
        item = lines[index]
        text = badge_text(item)
        if not text:
            break
        ib = item.get("bounds") or {}
        try:
            below = lines[index - 1].get("bounds") or {} if index > 0 else {}
            vertical = index == len(lines) - 1 or int(ib["top"]) >= int(below["bottom"]) - 2
        except (KeyError, TypeError, ValueError):
            return None, True
        if not vertical:
            return None, True
        candidates.append((text, ib))
    if not candidates or len(candidates) == len(lines):
        # A merged OCR line can contain message words followed by badge words.
        # Only remove a suffix when word-level boxes independently overlap a
        # detected badge region; uppercase text alone is never sufficient.
        marked: list[str] = []
        for item in reversed(lines):
            words = item.get("business_label_words") or []
            if not words:
                break
            marked[0:0] = [str(word).strip() for word in words if str(word).strip()]
        if not marked or not original:
            return None, False
        suffix = " ".join(marked)
        match = re.search(r"(?:^|\s)" + re.escape(suffix) + r"\s*$", original, flags=re.IGNORECASE)
        if not match:
            return None, True
        cleaned = original[:match.start()].rstrip(" \t-–—|•")
        return (cleaned or None), False
    base = lines[len(lines) - len(candidates) - 1].get("bounds") or {}
    try:
        for _text, bounds in candidates:
            aligned = abs(int(bounds["left"]) - int(base["left"])) <= 24
            shorter = (int(bounds["right"]) - int(bounds["left"])) <= max(80, (int(base["right"]) - int(base["left"])) * 0.9)
            if not (aligned and shorter):
                return None, True
    except (KeyError, TypeError, ValueError):
        return None, True
    suffix = " ".join(text for text, _bounds in reversed(candidates))
    match = re.search(r"(?:^|\s)" + re.escape(suffix) + r"\s*$", original)
    if not match:
        return None, True
    cleaned = original[:match.start()].rstrip(" \t-–—|•")
    return (cleaned or None), False


def _clear_business_labels(db: Session, captures: list, reviewer: str) -> dict:
    inspected = updated = unchanged = manual = 0
    for capture in captures:
        if capture.status != "completed" or capture.trashed_at is not None:
            continue
        result = ev.latest_result(capture)
        for row in (result.rows if result else []):
            inspected += 1
            if db.scalar(select(OcrFieldVerification).where(
                OcrFieldVerification.observation_id == row.id,
                OcrFieldVerification.field_name == "message",
                OcrFieldVerification.status == "verified",
            )):
                unchanged += 1
                continue
            cleaned, uncertain = _business_label_cleanup(row)
            if cleaned and cleaned != (row.message_preview or ""):
                if _save_auto_verification(db, row, "message", cleaned, reviewer):
                    updated += 1
                else:
                    unchanged += 1
            else:
                unchanged += 1
                manual += int(uncertain)
    db.commit()
    return {"rows_inspected": inspected, "labels_removed": updated, "messages_updated": updated,
            "rows_unchanged": unchanged, "rows_manual_review": manual}


def _save_auto_verification(db: Session, row, field_name: str, value: str, reviewer: str) -> bool:
    label = db.scalar(select(OcrFieldVerification).where(
        OcrFieldVerification.observation_id == row.id,
        OcrFieldVerification.field_name == field_name,
    ).with_for_update())
    if label and label.status == "verified" and label.verified_value == value:
        return False
    observed = {"phone": row.phone_candidate_raw, "message": row.message_preview}[field_name]
    payload = {"field_name": field_name, "observed_value": observed, "verified_value": value,
               "status": "verified", "dataset_split": "unassigned", "approved_for_dataset": False}
    if label is None:
        label = OcrFieldVerification(observation_id=row.id, revision=1, reviewer=reviewer, **payload)
        db.add(label)
        db.flush()
    else:
        label.revision += 1
        label.reviewer = reviewer
        for key, item in payload.items():
            setattr(label, key, item)
    db.add(OcrFieldVerificationAudit(verification_id=label.id, revision=label.revision, payload=payload, reviewer=reviewer))
    return True

class FieldVerificationBody(BaseModel):
    position: int = Field(ge=1)
    field_name: Literal["phone", "message", "timestamp"]
    status: Literal["verified", "missing", "unreadable", "incorrect"]
    verified_value: str | None = Field(default=None, max_length=5000)
    dataset_split: Literal["unassigned", "training", "evaluation"] = "unassigned"
    approved_for_dataset: bool = False
    reviewer: str = Field(min_length=1, max_length=120)
    expected_revision: int | None = Field(default=None, ge=1)


def _json(content, status: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status, headers=NO_STORE)


@router.get("")
def list_saved(search: str = Query(default="", max_length=200), status: str = Query(default="all"),
               page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=25, ge=1, le=100),
               db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    recover_stale_processing(db, settings)  # orphaned "processing" captures become retryable failures
    items = ev.list_evidence(db, search, status, offset=(page - 1) * page_size, limit=page_size)
    return _json({"items": [ev.serialize_summary(c) for c in items], "page": page, "page_size": page_size,
                  "has_more": len(items) == page_size})


@router.get("/stats")
def stats(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return _json(ev.evidence_stats(db, settings))


@router.get("/export")
def export(include_trash: bool = False, db: Session = Depends(get_db)):
    return JSONResponse(content=ev.export_evidence(db, include_trash), headers={**NO_STORE, "Content-Disposition": 'attachment; filename="gharpayy-local-review.json"'})


@router.post("/purge-trash")
def purge(body: PurgeBody, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return _json({"purged": ev.purge_trash(db, settings, body.reviewer)})


@router.get("/{capture_id}")
def detail(capture_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    capture = ev.get_capture(db, capture_id)
    return _json(ev.serialize_detail(db, capture, settings))


@router.get("/{capture_id}/image")
def image(capture_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    capture = ev.get_capture(db, capture_id)
    data = ev.evidence_path(settings, capture).read_bytes()
    return Response(content=data, media_type=capture.mime_type, headers={
        **NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'", "Content-Disposition": "inline"})


@router.post("/clear-message-all")
def clear_message_all(body: ClearMessageBody, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    captures = ev.list_evidence(db, status_filter="all", limit=ev.LIST_LIMIT)
    stats = _clear_business_labels(db, captures, body.reviewer.strip()[:120])
    return _json({"message": "WhatsApp Business label cleanup completed", **stats,
                  "screenshots_processed": len(captures), "screenshots_updated": sum(1 for c in captures if c.status == "completed"),
                  "review_items": [], "failures": []})
    # Legacy provider implementation below is unreachable.
    if not settings.ocr_external_transmission_enabled:
        raise ApiError(403, "EXTERNAL_TRANSMISSION_DISABLED", "External screenshot transmission is disabled.")
    if not settings.groq_api_key:
        raise ApiError(503, "GROQ_NOT_CONFIGURED", "Groq Vision is not configured.")
    captures = ev.list_evidence(db, status_filter="all", limit=ev.LIST_LIMIT)
    eligible = []
    for capture in captures:
        if capture.status != "completed":
            continue
        result = ev.latest_result(capture)
        for row in (result.rows if result else []):
            verified = {item.field_name for item in row.field_verifications if item.status == "verified"}
            if "phone" in verified and "message" in verified:
                continue
            eligible.append((capture, row))
    batches, current, size = [], [], 0
    for item in eligible:
        encoded_size = len(str(item[1].phone_candidate_raw or "")) + len(str(item[1].message_preview or "")) + 100
        if current and (len(current) >= 40 or size + encoded_size > 90_000):
            batches.append(current); current, size = [], 0
        current.append(item); size += encoded_size
    if current:
        batches.append(current)
    cleaned_messages = formatted_phones = analyzed = rows_analyzed = 0
    review_items, failures, diagnostics = [], [], []
    stopped_on_rate_limit = False
    for batch_number, batch in enumerate(batches, 1):
        if stopped_on_rate_limit:
            break
        request_rows = {f"r{index}": row for index, (_, row) in enumerate(batch, 1)}
        rows = [{"row_id": request_id,
                 "phone": None if "phone" in {item.field_name for item in row.field_verifications if item.status == "verified"} else row.phone_candidate_raw,
                 "message": None if "message" in {item.field_name for item in row.field_verifications if item.status == "verified"} else row.message_preview}
                for request_id, row in request_rows.items()]
        cloud = groq_text_cleanup(settings.groq_api_key, settings.ocr_cloud_timeout_seconds, settings.groq_vision_model, rows)
        if cloud.status == "failed":
            failure = {"batch": batch_number, "provider": "groq", "status": "failure", "phase": cloud.phase,
                       "category": cloud.error, "status_code": cloud.status_code, "safe_corrections": 0, "review_proposals": 0}
            failures.append(failure); diagnostics.append(failure)
            db.rollback()
            if cloud.status_code == 429 or cloud.error in ("quota", "rate_limit"):
                stopped_on_rate_limit = True
            continue
        by_id = {row.id: (capture, row) for capture, row in batch}
        pending, batch_reviews = [], []
        for observation in cloud.observations or []:
            request_id = observation["row_id"]
            row = request_rows[request_id]
            capture = next(capture for capture, candidate in batch if candidate is row)
            phone = _safe_phone_format(row.phone_candidate_raw, observation.get("phone"))
            message = _safe_message_cleanup(row.message_preview, observation.get("message"), bool(observation.get("uncertain", True)))
            if phone: pending.append((row, "phone", phone))
            elif isinstance(observation.get("phone"), str) and observation["phone"].strip() != (row.phone_candidate_raw or ""):
                batch_reviews.append({"capture_id": capture.id, "position": row.position, "field_name": "phone"})
            if message: pending.append((row, "message", message))
            elif isinstance(observation.get("message"), str) and observation["message"].strip() != (row.message_preview or ""):
                batch_reviews.append({"capture_id": capture.id, "position": row.position, "field_name": "message"})
        try:
            for row, field_name, value in pending:
                if _save_auto_verification(db, row, field_name, value, body.reviewer.strip()[:120]):
                    cleaned_messages += field_name == "message"; formatted_phones += field_name == "phone"
            db.commit()
            analyzed += 1; rows_analyzed += len(batch); review_items.extend(batch_reviews)
            diagnostics.append({"batch": batch_number, "provider": "groq", "status": "success", "phase": cloud.phase,
                                "category": None, "status_code": cloud.status_code, "safe_corrections": len(pending),
                                "review_proposals": len(batch_reviews)})
        except Exception:
            db.rollback()
            cleaned_messages -= sum(field == "message" for _, field, _ in pending)
            formatted_phones -= sum(field == "phone" for _, field, _ in pending)
            failures.append({"batch": batch_number, "provider": "groq", "status": "failure", "phase": "storage",
                             "category": "storage_error", "status_code": None, "safe_corrections": 0, "review_proposals": 0})
    failed_batches = len(failures)
    successful_batches = analyzed
    return _json({"message": "Captured messages cleaned" if (cleaned_messages or formatted_phones) else "No safe changes found.",
                  "messages_cleaned": cleaned_messages, "phones_formatted": formatted_phones,
                  "review_count": len(review_items), "review_unavailable_count": len(failures),
                  "rows_analyzed": rows_analyzed, "batches_successful": successful_batches,
                  "batches_failed": failed_batches, "batches_total": len(batches), "review_items": review_items[:100],
                  "failures": failures, "diagnostics": diagnostics, "rate_limited": stopped_on_rate_limit,
                  "screenshots_processed": len({capture.id for capture, _ in eligible}),
                  "screenshots_analyzed": len({capture.id for capture, _ in eligible}) if not failures else 0,
                  "screenshots_failed": len(failures)})

@router.post("/{capture_id}/clear-message")
def clear_message(capture_id: str, body: ClearMessageBody, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    capture = ev.get_capture(db, capture_id)
    stats = _clear_business_labels(db, [capture], body.reviewer.strip()[:120])
    return _json({"capture_id": capture.id, "message": "WhatsApp Business label cleanup completed", **stats,
                  "proposals": [], "diagnostics": {"candidate_count": stats["rows_inspected"], "returned_count": stats["labels_removed"],
                                                     "unmatched_count": 0, "rejected_count": stats["rows_manual_review"]}})
    # Legacy provider implementation below is unreachable.
    if not settings.ocr_external_transmission_enabled:
        raise ApiError(403, "EXTERNAL_TRANSMISSION_DISABLED", "External screenshot transmission is disabled.")
    if not settings.groq_api_key:
        raise ApiError(503, "GROQ_NOT_CONFIGURED", "Groq Vision is not configured.")
    capture = ev.get_capture(db, capture_id)
    if capture.status != "completed" or capture.trashed_at is not None:
        raise ApiError(409, "EVIDENCE_NOT_READY", "Only active completed evidence can be reviewed.")
    result = ev.latest_result(capture)
    if result is None:
        raise ApiError(404, "OCR_NOT_FOUND", "No OCR result is available.")
    candidates = [row for row in result.rows if row.warnings or row.phone_status != "candidate" or not row.message_preview]
    if not candidates:
        candidates = list(result.rows)
    request_rows = {f"r{index}": row for index, row in enumerate(candidates, 1)}
    text_rows = [{"row_id": request_id, "phone": row.phone_candidate_raw, "message": row.message_preview}
                 for request_id, row in request_rows.items()]
    cloud = groq_text_cleanup(settings.groq_api_key, settings.ocr_cloud_timeout_seconds,
                              settings.groq_vision_model, text_rows)
    if cloud.status == "failed":
        detail = f"Groq Clear Message failed [{cloud.phase}]: {cloud.error}"
        if cloud.status_code is not None:
            detail += f" ({cloud.status_code})"
        code = "GROQ_CLEAR_MESSAGE_INVALID_ROWS" if cloud.error == "invalid_row_ids" else "GROQ_CLEAR_MESSAGE_FAILED"
        raise ApiError(502, code, detail)
    proposals = []
    for observation in cloud.observations or []:
        row = request_rows.get(observation.get("row_id"))
        if row is None:
            continue
        for field, original, suggested in (
            ("phone", row.phone_candidate_raw, observation.get("phone")),
            ("message", row.message_preview, observation.get("message")),
        ):
            if isinstance(suggested, str) and suggested.strip() and suggested.strip() != (original or ""):
                proposals.append({"position": row.position, "field_name": field, "original_value": original,
                                  "suggested_value": suggested.strip(), "confidence": observation.get("confidence"),
                                  "uncertain": bool(observation.get("uncertain", True))})
    return _json({"capture_id": capture.id, "model": settings.groq_vision_model, "proposals": proposals,
                  "diagnostics": {"candidate_count": len(candidates), "returned_count": len(cloud.observations or []),
                                  "unmatched_count": 0, "rejected_count": 0}})


@router.patch("/{capture_id}")
def update(capture_id: str, body: EvidencePatch, db: Session = Depends(get_db)):
    capture = ev.get_capture(db, capture_id)
    names = [(n.position, n.name) for n in body.observed_names] if body.observed_names else None
    ev.update_evidence(db, capture, reviewer=body.reviewer, screenshot_label=body.screenshot_label, text=body.text,
                       observed_names=names, expected_revision=body.expected_revision)
    return _json(ev.serialize_detail(db, capture))


@router.post("/{capture_id}/review")
def review(capture_id: str, body: ReviewBody, db: Session = Depends(get_db)):
    capture = ev.get_capture(db, capture_id)
    ev.mark_reviewed(db, capture, body.reviewer, body.expected_revision)
    return _json(ev.serialize_detail(db, capture))

@router.post("/{capture_id}/field-verifications")
def field_verification(capture_id: str, body: FieldVerificationBody, db: Session = Depends(get_db)):
    capture = ev.get_capture(db, capture_id)
    result = ev.latest_result(capture)
    row = next((item for item in (result.rows if result else []) if item.position == body.position), None)
    if row is None: raise ApiError(404, "NOT_FOUND", "Evidence was not found.")
    if body.status in ("verified", "incorrect") and not (body.verified_value and body.verified_value.strip()): raise ApiError(422, "INVALID_LABEL", "A human-entered value is required for this status.")
    if body.status in ("missing", "unreadable") and body.verified_value: raise ApiError(422, "INVALID_LABEL", "Missing or unreadable fields cannot contain a value.")
    label = db.scalar(select(OcrFieldVerification).where(OcrFieldVerification.observation_id == row.id, OcrFieldVerification.field_name == body.field_name).with_for_update())
    if label and body.expected_revision is not None and label.revision != body.expected_revision: raise ApiError(409, "EDIT_CONFLICT", "This label changed in another tab.")
    observed = {"phone": row.phone_candidate_raw, "message": row.message_preview, "timestamp": row.visible_timestamp_text}[body.field_name]
    payload = {"field_name": body.field_name, "observed_value": observed, "verified_value": body.verified_value.strip() if body.verified_value else None, "status": body.status, "dataset_split": body.dataset_split, "approved_for_dataset": body.approved_for_dataset}
    reviewer = body.reviewer.strip()
    if label is None:
        label = OcrFieldVerification(observation_id=row.id, revision=1, reviewer=reviewer, **payload); db.add(label); db.flush()
    else:
        label.revision += 1; label.reviewer = reviewer
        for key, value in payload.items(): setattr(label, key, value)
    db.add(OcrFieldVerificationAudit(verification_id=label.id, revision=label.revision, payload=payload, reviewer=reviewer)); db.commit()
    return _json(ev.serialize_detail(db, capture))


@router.post("/{capture_id}/trash")
def trash(capture_id: str, body: ReasonBody, db: Session = Depends(get_db)):
    capture = ev.get_capture(db, capture_id)
    ev.set_trash(db, capture, body.reason, body.reviewer, restore=False, expected_revision=body.expected_revision)
    return _json(ev.serialize_detail(db, capture))


@router.post("/{capture_id}/restore")
def restore(capture_id: str, body: ReasonBody, db: Session = Depends(get_db)):
    capture = ev.get_capture(db, capture_id)
    ev.set_trash(db, capture, body.reason, body.reviewer, restore=True, expected_revision=body.expected_revision)
    return _json(ev.serialize_detail(db, capture))


@router.post("/{capture_id}/proposals")
def proposals(capture_id: str, body: ProposalBody, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    capture = ev.get_capture(db, capture_id)
    ev.add_region_proposals(db, settings, capture, body.kind, body.region.model_dump())
    return _json(ev.serialize_detail(db, capture))


@router.post("/{capture_id}/retry")
async def retry(capture_id: str, body: RetryBody, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                engine_factory: EngineFactory = Depends(get_engine_factory)):
    capture = ev.get_capture(db, capture_id)
    if capture.status != "failed" or capture.trashed_at is not None:
        raise ApiError(409, "NOT_RETRYABLE", "Only failed, active readings can be retried.")
    image = validate_image(ev.evidence_path(settings, capture).read_bytes(), capture.mime_type, image_limits(settings))
    ev.claim_revision(db, capture, body.expected_revision)
    if capture.status != "failed" or capture.trashed_at is not None:  # re-check on the locked row
        db.rollback()
        raise ApiError(409, "NOT_RETRYABLE", "Only failed, active readings can be retried.")
    capture.status = "processing"
    db.add(EvidenceAuditEvent(capture_id=capture.id, capture_sha256=capture.sha256, action="retry", reviewer=ev.reviewer_name(body.reviewer)))
    db.commit()
    try:
        await run_ocr_bounded(settings, run_ocr_attempt, db, capture, image, settings, engine_factory)
    except ApiError as exc:
        if exc.code == "OCR_BUSY":
            capture.status = "failed"
            db.commit()
        raise
    return _json(ev.serialize_detail(db, capture))
