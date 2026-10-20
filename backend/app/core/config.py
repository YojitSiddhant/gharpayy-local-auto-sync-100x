from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    backend_cors_origins: list[str] = ["http://localhost:3000"]
    database_url: str = "mysql+pymysql://username:password@localhost:3306/gharpayy_local_sync"

    # Shared secret between the Next.js server (proxy) and FastAPI. Never exposed to the browser.
    # OCR endpoints fail closed when it is missing or shorter than 32 characters.
    local_api_token: str = ""
    # Host headers accepted by protected endpoints (DNS-rebinding guard). Ports are ignored.
    backend_allowed_hosts: list[str] = ["localhost", "127.0.0.1", "::1"]

    ocr_max_upload_bytes: int = 10 * 1024 * 1024
    ocr_max_image_pixels: int = 25_000_000
    ocr_max_image_side: int = 10_000
    ocr_min_image_side: int = 32
    ocr_tesseract_timeout_seconds: float = 20.0
    ocr_tesseract_psm: int = 11
    ocr_max_concurrency: int = 1
    ocr_queue_wait_seconds: float = 10.0

    # Private evidence storage. Must stay outside public/static directories.
    screenshot_storage_dir: str = "backend/.private_storage/screenshots"
    screenshot_retention_enabled: bool = True
    # Evidence store limits (reference defaults: 200 records incl. trash, 100 MB). Trash counts until purged.
    evidence_max_records: int = 200
    evidence_max_bytes: int = 100 * 1024 * 1024
    evidence_json_body_limit: int = 1024 * 1024

    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")

    @field_validator("backend_cors_origins", "backend_allowed_hosts", mode="before")
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
