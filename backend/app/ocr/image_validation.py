"""Screenshot upload validation: signatures, decoding, size and decompression-bomb limits.

Validation never logs or returns image content. Pixel geometry is preserved exactly
(no EXIF rotation), so every coordinate reported later refers to the stored file.
"""
from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from backend.app.core.errors import ApiError

ALLOWED_MIME_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int
    max_pixels: int
    max_side: int
    min_side: int


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    sha256: str
    mime_type: str
    image_format: str
    width: int
    height: int

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.image_format]


def sniff_format(data: bytes) -> str | None:
    """Identify PNG/JPEG/WebP from magic bytes only."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def normalize_mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def validate_image(data: bytes, content_type: str | None, limits: ImageLimits) -> ValidatedImage:
    mime = normalize_mime(content_type)
    if mime not in ALLOWED_MIME_TYPES:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Only PNG, JPEG or WebP screenshots are accepted.")
    if not data:
        raise ApiError(400, "EMPTY_UPLOAD", "The uploaded file is empty.")
    if len(data) > limits.max_bytes:
        raise ApiError(413, "UPLOAD_TOO_LARGE", f"The screenshot exceeds the {limits.max_bytes // (1024 * 1024)} MB limit.")

    sniffed = sniff_format(data)
    if sniffed is None:
        raise ApiError(415, "INVALID_SIGNATURE", "File content is not a PNG, JPEG or WebP image.")
    if sniffed != ALLOWED_MIME_TYPES[mime]:
        raise ApiError(415, "SIGNATURE_MISMATCH", "File content does not match its declared image type.")

    width, height = _checked_decode(data, sniffed, limits)
    return ValidatedImage(
        data=data, sha256=hashlib.sha256(data).hexdigest(), mime_type=mime,
        image_format=sniffed, width=width, height=height,
    )


def _check_dimensions(width: int, height: int, limits: ImageLimits) -> None:
    if width < limits.min_side or height < limits.min_side:
        raise ApiError(422, "IMAGE_TOO_SMALL", f"Image must be at least {limits.min_side} × {limits.min_side} pixels.")
    if width > limits.max_side or height > limits.max_side or width * height > limits.max_pixels:
        raise ApiError(413, "IMAGE_DIMENSIONS_TOO_LARGE", "Image dimensions exceed the allowed limit.")


def _checked_decode(data: bytes, expected_format: str, limits: ImageLimits) -> tuple[int, int]:
    with warnings.catch_warnings():
        # Treat Pillow's decompression-bomb warning as fatal, and cap Pillow's own guard too.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            # Header-only open: dimensions are known before any pixel data is decoded.
            with Image.open(io.BytesIO(data)) as header:
                if header.format != expected_format:
                    raise ApiError(415, "SIGNATURE_MISMATCH", "File content does not match its declared image type.")
                width, height = header.size
                _check_dimensions(width, height, limits)
                if getattr(header, "n_frames", 1) > 1:
                    raise ApiError(422, "ANIMATED_IMAGE_NOT_SUPPORTED", "Animated images are not supported.")
                header.verify()
            # verify() leaves the image unusable; reopen and fully decode to catch truncation.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.size != (width, height):
                    raise ApiError(422, "CORRUPTED_IMAGE", "The image could not be decoded.")
        except ApiError:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise ApiError(413, "IMAGE_DIMENSIONS_TOO_LARGE", "Image dimensions exceed the allowed limit.") from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError):
            raise ApiError(422, "CORRUPTED_IMAGE", "The image could not be decoded.") from None
    return width, height


def decode_rgb(data: bytes) -> Image.Image:
    """Decode an already-validated image to RGB without changing its geometry."""
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")
