import io
import struct
import zlib

import pytest
from PIL import Image

from backend.app.core.errors import ApiError
from backend.app.ocr.image_validation import ImageLimits, sniff_format, validate_image
from backend.tests.conftest import image_bytes

LIMITS = ImageLimits(max_bytes=10 * 1024 * 1024, max_pixels=25_000_000, max_side=10_000, min_side=32)


def error_code(data: bytes, content_type: str, limits: ImageLimits = LIMITS) -> str:
    with pytest.raises(ApiError) as exc:
        validate_image(data, content_type, limits)
    return exc.value.code


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_accepts_supported_formats(fmt, mime):
    result = validate_image(image_bytes(fmt), mime, LIMITS)
    assert (result.width, result.height, result.image_format) == (360, 640, fmt)
    assert len(result.sha256) == 64


def test_rejects_unsupported_mime_type():
    assert error_code(image_bytes("GIF"), "image/gif") == "UNSUPPORTED_MEDIA_TYPE"
    assert error_code(b"%PDF-1.4", "application/pdf") == "UNSUPPORTED_MEDIA_TYPE"


def test_rejects_wrong_signature_even_with_image_mime():
    assert error_code(b"<html>not an image</html>", "image/png") == "INVALID_SIGNATURE"


def test_rejects_signature_that_disagrees_with_declared_type():
    assert error_code(image_bytes("JPEG"), "image/png") == "SIGNATURE_MISMATCH"


def test_rejects_empty_upload():
    assert error_code(b"", "image/png") == "EMPTY_UPLOAD"


def test_rejects_oversized_upload():
    small = ImageLimits(max_bytes=100, max_pixels=LIMITS.max_pixels, max_side=LIMITS.max_side, min_side=LIMITS.min_side)
    assert error_code(image_bytes("PNG"), "image/png", small) == "UPLOAD_TOO_LARGE"


def test_rejects_truncated_corrupted_image():
    data = image_bytes("PNG", (400, 400), "red")
    assert error_code(data[: len(data) // 2], "image/png") == "CORRUPTED_IMAGE"
    assert error_code(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png") == "CORRUPTED_IMAGE"


def test_rejects_excessive_dimensions():
    assert error_code(image_bytes("PNG", (10_001, 40)), "image/png") == "IMAGE_DIMENSIONS_TOO_LARGE"
    tight = ImageLimits(max_bytes=LIMITS.max_bytes, max_pixels=100_000, max_side=LIMITS.max_side, min_side=32)
    assert error_code(image_bytes("PNG", (400, 400)), "image/png", tight) == "IMAGE_DIMENSIONS_TOO_LARGE"


def test_rejects_too_small_image():
    assert error_code(image_bytes("PNG", (10, 10)), "image/png") == "IMAGE_TOO_SMALL"


def _png_header_only(width: int, height: int) -> bytes:
    """A tiny file claiming huge dimensions: a decompression-bomb shape."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b"")


def test_rejects_decompression_bomb_before_decoding():
    bomb = _png_header_only(9_000, 9_000)
    assert len(bomb) < 200
    assert error_code(bomb, "image/png") == "IMAGE_DIMENSIONS_TOO_LARGE"


def test_rejects_animated_images():
    frames = [Image.new("RGB", (64, 64), c) for c in ("red", "blue")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:])
    assert error_code(buffer.getvalue(), "image/webp") == "ANIMATED_IMAGE_NOT_SUPPORTED"


def test_sniff_format_ignores_mime():
    assert sniff_format(image_bytes("WEBP")) == "WEBP"
    assert sniff_format(b"GIF89a") is None
