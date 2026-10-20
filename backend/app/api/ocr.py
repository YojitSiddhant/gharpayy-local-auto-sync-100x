from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ApiError
from backend.app.core.security import require_local_api_access
from backend.app.core.auth import require_super_admin
from backend.app.db.session import get_db
from backend.app.ocr.concurrency import run_ocr_bounded
from backend.app.ocr.image_validation import ImageLimits, validate_image
from backend.app.ocr.service import CaptureMeta, EngineFactory, default_engine, process_screenshot
from backend.app.schemas.ocr import build_extract_response

router = APIRouter(prefix="/ocr", tags=["ocr"], dependencies=[Depends(require_local_api_access), Depends(require_super_admin)])

ACCOUNT_HEADER = "x-account-label"
FILENAME_HEADER = "x-filename"
MAX_ACCOUNT_LABEL = 90
MAX_FILENAME = 255
SOURCE_MODES = {"manual-file", "screen-capture"}


def get_engine_factory() -> EngineFactory:
    return default_engine


def _header_text(request: Request, name: str, limit: int) -> str | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    try:
        value = unquote(raw, errors="strict").strip()
    except UnicodeDecodeError:
        raise ApiError(400, "INVALID_HEADER", f"Header {name} is not valid UTF-8.") from None
    if len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ApiError(400, "INVALID_HEADER", f"Header {name} is invalid.")
    return value or None


def _header_int(request: Request, name: str) -> int | None:
    raw = request.headers.get(name)
    if raw is None:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 100_000:
        raise ApiError(400, "INVALID_HEADER", f"Header {name} is invalid.")
    return int(raw)


def _capture_meta(request: Request) -> CaptureMeta:
    mode = request.headers.get("x-source-mode", "manual-file")
    if mode not in SOURCE_MODES:
        raise ApiError(400, "INVALID_HEADER", "Header x-source-mode is invalid.")
    captured_at = None
    if raw := request.headers.get("x-captured-at"):
        try:
            parsed = datetime.fromisoformat(raw[:40])
        except ValueError:
            raise ApiError(400, "INVALID_HEADER", "Header x-captured-at is invalid.") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ApiError(400, "INVALID_HEADER", "Header x-captured-at is in the future.")
        captured_at = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    width, height = _header_int(request, "x-original-width"), _header_int(request, "x-original-height")
    crop = None
    if raw := request.headers.get("x-crop"):
        parts = raw.split(",")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            raise ApiError(400, "INVALID_HEADER", "Header x-crop is invalid.")
        x0, y0, x1, y1 = map(int, parts)
        if not (x1 > x0 and y1 > y0 and (width is None or x1 <= width) and (height is None or y1 <= height)):
            raise ApiError(400, "INVALID_HEADER", "Header x-crop is outside the source.")
        crop = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    return CaptureMeta(
        source_mode=mode, captured_at=captured_at, source_label=_header_text(request, "x-source-label", 255),
        screenshot_label=_header_text(request, "x-screenshot-label", 255),
        original_width=width, original_height=height, crop=crop,
    )


async def _read_capped_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise ApiError(413, "UPLOAD_TOO_LARGE", f"The screenshot exceeds the {limit // (1024 * 1024)} MB limit.")
        except ValueError:
            raise ApiError(400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length header.") from None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "UPLOAD_TOO_LARGE", f"The screenshot exceeds the {limit // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def image_limits(settings: Settings) -> ImageLimits:
    return ImageLimits(settings.ocr_max_upload_bytes, settings.ocr_max_image_pixels, settings.ocr_max_image_side, settings.ocr_min_image_side)


@router.post("/extract")
async def extract_screenshot(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    engine_factory: EngineFactory = Depends(get_engine_factory),
) -> JSONResponse:
    """Body: raw image bytes. Headers: Content-Type (image/png|jpeg|webp), X-Account-Label, optional X-Filename,
    and optional capture metadata (X-Source-Mode, X-Captured-At, X-Source-Label, X-Screenshot-Label,
    X-Original-Width, X-Original-Height, X-Crop) — all URI-encoded where textual."""
    account_label = _header_text(request, ACCOUNT_HEADER, MAX_ACCOUNT_LABEL)
    if not account_label:
        raise ApiError(400, "ACCOUNT_LABEL_REQUIRED", "A WhatsApp account label is required.")
    filename = _header_text(request, FILENAME_HEADER, MAX_FILENAME)
    meta = _capture_meta(request)
    data = await _read_capped_body(request, settings.ocr_max_upload_bytes)
    image = await run_in_threadpool(validate_image, data, request.headers.get("content-type"), image_limits(settings))
    capture, duplicate = await run_ocr_bounded(settings, process_screenshot, db, image, account_label, filename, settings, engine_factory, meta)
    body = build_extract_response(capture, duplicate).model_dump(mode="json")
    return JSONResponse(status_code=200 if duplicate else 201, content=body, headers={"Cache-Control": "no-store"})
