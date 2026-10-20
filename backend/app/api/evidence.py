"""Protected saved-evidence API (list, detail, image, review, corrections, trash, purge, export).

Same local-only boundary as OCR upload. Responses never contain file paths; images are served
only through this authenticated route, never from a static directory.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.ocr import get_engine_factory, image_limits
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.db.session import get_db
from backend.app.models.ocr import EvidenceAuditEvent
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


def _json(content, status: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status, headers=NO_STORE)


@router.get("")
def list_saved(search: str = Query(default="", max_length=200), status: str = Query(default="all"),
               db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    recover_stale_processing(db, settings)  # orphaned "processing" captures become retryable failures
    return _json({"items": [ev.serialize_summary(c) for c in ev.list_evidence(db, search, status)]})


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
def detail(capture_id: str, db: Session = Depends(get_db)):
    return _json(ev.serialize_detail(db, ev.get_capture(db, capture_id)))


@router.get("/{capture_id}/image")
def image(capture_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    capture = ev.get_capture(db, capture_id)
    data = ev.evidence_path(settings, capture).read_bytes()
    return Response(content=data, media_type=capture.mime_type, headers={
        **NO_STORE, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'", "Content-Disposition": "inline"})


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
