from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.health import health, router as health_router
from backend.app.core.config import get_settings
from backend.app.core.errors import unhandled_exception_handler

settings = get_settings()
app = FastAPI(title="Gharpayy Local Auto Sync API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.backend_cors_origins, allow_credentials=True, allow_methods=["GET"], allow_headers=["*"])
app.add_exception_handler(Exception, unhandled_exception_handler)
app.include_router(health_router, prefix="/api/v1")
app.add_api_route("/health", health, methods=["GET"])
