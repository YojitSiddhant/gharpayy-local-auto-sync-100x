from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Deterministic API error: stable machine code, safe human message, no private content."""

    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
    if exc.details:
        body["error"]["details"] = exc.details
    return JSONResponse(status_code=exc.status_code, content=body, headers={"Cache-Control": "no-store"})


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Field names only; submitted values (which may contain private OCR text) are never echoed.
    fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) for err in getattr(exc, "errors", lambda: [])()})
    return JSONResponse(status_code=422, headers={"Cache-Control": "no-store"},
                        content={"error": {"code": "INVALID_REQUEST", "message": "Request is invalid.", "details": {"fields": fields}}})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
