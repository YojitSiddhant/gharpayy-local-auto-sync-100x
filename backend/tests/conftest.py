"""Shared fixtures. All images are synthetic; no real WhatsApp screenshots are used."""
from __future__ import annotations

import io
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.api.ocr import get_engine_factory
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import engine, get_db
from backend.app.main import app
from backend.app.ocr.engine import OcrLine, OcrOutput, OcrWord

TEST_TOKEN = "test-token-" + "x" * 40


def mysql_unavailable(reason: str) -> None:
    """Skip MySQL-backed tests locally, but FAIL when REQUIRE_MYSQL_TESTS=1 so missing coverage is never silent."""
    if os.environ.get("REQUIRE_MYSQL_TESTS") == "1":
        pytest.fail(f"MySQL-backed test required but unavailable: {reason}")
    pytest.skip(f"MySQL-backed test skipped: {reason} (set REQUIRE_MYSQL_TESTS=1 to make this a failure)")


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (360, 640), color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def chat_list_image(fmt: str = "PNG") -> bytes:
    """Synthetic, privacy-safe WhatsApp-like chat list (fictional numbers and text)."""
    image = Image.new("RGB", (720, 1280), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    small = ImageFont.load_default(size=26)
    draw.text((40, 40), "Chats", fill="black", font=font)
    rows = [
        ("+91 98765 43210", "2:52 pm", "Need a private room near office"),
        ("Akhil Kumar", "1:15 pm", "Call me on +91 94491 90529 please"),
        ("+91 98765 4321", "Yesterday", "Is the room still available..."),
    ]
    y = 180
    for header, stamp, preview in rows:
        draw.ellipse((30, y - 5, 110, y + 75), fill="#cccccc")
        draw.text((140, y), header, fill="black", font=font)
        draw.text((560, y + 4), stamp, fill="#555555", font=small)
        draw.text((140, y + 48), preview, fill="#444444", font=small)
        y += 170
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def make_line(text: str, left: int, top: int, confidence: float = 92.0, height: int = 30) -> OcrLine:
    right = left + 16 * len(text)
    return OcrLine(text=text, confidence=confidence, left=left, top=top, right=right, bottom=top + height,
                   words=[OcrWord(text=text, confidence=confidence, left=left, top=top, right=right, bottom=top + height)])


def fake_chat_lines() -> list[OcrLine]:
    return [
        make_line("Chats", 40, 40),
        make_line("+91 98765 43210", 140, 180), make_line("2:52 pm", 560, 184), make_line("Need a private room", 140, 228),
        make_line("Akhil Kumar", 140, 350), make_line("1:15 pm", 560, 354), make_line("Call me on +91 94491 90529", 140, 398),
    ]


class FakeEngine:
    def __init__(self, lines: list[OcrLine] | None = None, error: Exception | None = None) -> None:
        self.lines = fake_chat_lines() if lines is None else lines
        self.error = error
        self.calls = 0

    def run(self, image: Image.Image) -> OcrOutput:
        self.calls += 1
        if self.error:
            raise self.error
        return OcrOutput(raw_text="\n".join(l.text for l in self.lines), lines=self.lines, mean_confidence=92.0,
                         word_count=len(self.lines), engine_version="5.5.3", language="eng", psm=11,
                         processing_scale=1.0, preprocessing=["grayscale"], duration_ms=5)


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, app_env="test", local_api_token=TEST_TOKEN,
                    backend_cors_origins=["http://localhost:3000"], screenshot_storage_dir=str(tmp_path / "evidence"))


@pytest.fixture
def mysql_session() -> Iterator[Session]:
    """Transactional session on the dev MySQL database; everything is rolled back afterwards."""
    try:
        connection = engine.connect()
    except SQLAlchemyError:
        mysql_unavailable("MySQL not reachable")
    outer = connection.begin()
    if not inspect(connection).has_table("screenshot_captures"):
        outer.rollback()
        connection.close()
        mysql_unavailable("evidence migrations not applied")
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def fake_engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def api(test_settings, fake_engine) -> Iterator[TestClient]:
    """Client with test settings and fake OCR; no database bound (validation-only paths)."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_engine_factory] = lambda: (lambda settings: fake_engine)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def db_api(api, mysql_session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: mysql_session
    return api


def upload_headers(content_type: str = "image/png", account: str = "Test WhatsApp", **extra: str) -> dict[str, str]:
    return {"content-type": content_type, "x-account-label": account, "x-local-api-token": TEST_TOKEN, **extra}
