from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.evidence import router as evidence_router
from backend.app.api.auth import router as auth_router
from backend.app.api.admin import router as admin_router
from backend.app.api.capture_sessions import router as capture_sessions_router
from backend.app.api.health import health, router as health_router
from backend.app.api.ocr import router as ocr_router
from backend.app.core.config import get_settings
from backend.app.core.errors import ApiError, api_error_handler, unhandled_exception_handler, validation_error_handler

settings = get_settings()
app = FastAPI(title="Gharpayy Local Auto Sync API", version="0.3.0")
# Browsers only call GET /health directly. OCR uploads go through the same-origin Next.js proxy,
# so CORS stays GET-only; CORS is not used as an authentication mechanism.
app.add_middleware(CORSMiddleware, allow_origins=settings.backend_cors_origins, allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(capture_sessions_router, prefix="/api/v1")
app.include_router(ocr_router, prefix="/api/v1")
app.include_router(evidence_router, prefix="/api/v1")
app.add_api_route("/health", health, methods=["GET"])
