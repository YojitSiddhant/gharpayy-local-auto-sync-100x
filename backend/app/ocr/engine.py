"""Local English Tesseract OCR. No hosted inference, no model downloads.

Preprocessing is deliberately minimal (grayscale, optional 2x upscale for small
screenshots). All coordinates are mapped back to original image pixels.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache

import pytesseract
from PIL import Image

OCR_LANGUAGE = "eng"
ENGINE_NAME = "tesseract"
SMALL_WIDTH_UPSCALE_BELOW = 650


class OcrError(Exception):
    code = "OCR_FAILED"


class OcrTimeoutError(OcrError):
    code = "OCR_TIMEOUT"


class OcrUnavailableError(OcrError):
    code = "OCR_ENGINE_UNAVAILABLE"


@dataclass
class OcrWord:
    text: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class OcrLine:
    text: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int
    words: list[OcrWord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text, "confidence": self.confidence,
            "bounds": {"left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom},
            "words": [
                {"text": w.text, "confidence": w.confidence,
                 "bounds": {"left": w.left, "top": w.top, "right": w.right, "bottom": w.bottom}}
                for w in self.words
            ],
        }


@dataclass
class OcrOutput:
    raw_text: str
    lines: list[OcrLine]
    mean_confidence: float | None
    word_count: int
    engine_version: str
    language: str
    psm: int
    processing_scale: float
    preprocessing: list[str]
    duration_ms: int


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError("Tesseract executable not found") from exc


def ensure_language_available() -> None:
    try:
        languages = pytesseract.get_languages(config="")
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError("Tesseract executable not found") from exc
    except Exception as exc:  # noqa: BLE001 - any failure here means the engine is unusable
        raise OcrUnavailableError("Tesseract language list unavailable") from exc
    if OCR_LANGUAGE not in languages:
        raise OcrUnavailableError("English Tesseract language data (eng) is not installed")


def preprocess(image: Image.Image) -> tuple[Image.Image, float, list[str]]:
    steps = ["grayscale"]
    working = image.convert("L")
    scale = 1.0
    if working.width < SMALL_WIDTH_UPSCALE_BELOW:
        scale = 2.0
        working = working.resize((round(working.width * scale), round(working.height * scale)), Image.Resampling.LANCZOS)
        steps.append("upscale_2x_lanczos")
    return working, scale, steps


def _group_lines(data: dict, scale: float) -> list[OcrLine]:
    groups: dict[tuple[int, int, int], list[OcrWord]] = {}
    for i, raw in enumerate(data.get("text", [])):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            confidence = max(0.0, float(data["conf"][i]))
        except (TypeError, ValueError):
            confidence = 0.0
        left, top = int(data["left"][i]), int(data["top"][i])
        width, height = int(data["width"][i]), int(data["height"][i])
        word = OcrWord(
            text=text, confidence=round(confidence, 1),
            left=round(left / scale), top=round(top / scale),
            right=round((left + width) / scale), bottom=round((top + height) / scale),
        )
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        groups.setdefault(key, []).append(word)
    lines = []
    for words in groups.values():
        words.sort(key=lambda w: w.left)
        lines.append(OcrLine(
            text=" ".join(w.text for w in words),
            confidence=round(sum(w.confidence for w in words) / len(words), 1),
            left=min(w.left for w in words), top=min(w.top for w in words),
            right=max(w.right for w in words), bottom=max(w.bottom for w in words),
            words=words,
        ))
    return sorted(lines, key=lambda line: (line.top, line.left))


def _reading_order_text(lines: list[OcrLine]) -> str:
    """Join lines top-to-bottom; lines sharing a visual row are joined left-to-right."""
    if not lines:
        return ""
    rows: list[list[OcrLine]] = []
    for line in lines:
        height = max(1, line.bottom - line.top)
        if rows and abs(line.top - rows[-1][0].top) <= height * 0.5:
            rows[-1].append(line)
        else:
            rows.append([line])
    return "\n".join("  ".join(l.text for l in sorted(row, key=lambda l: l.left)) for row in rows)


class TesseractEngine:
    def __init__(self, timeout_seconds: float, psm: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.psm = psm

    def run(self, image: Image.Image) -> OcrOutput:
        version = tesseract_version()
        ensure_language_available()
        working, scale, steps = preprocess(image)
        started = time.perf_counter()
        try:
            data = pytesseract.image_to_data(
                working, lang=OCR_LANGUAGE, config=f"--psm {self.psm}",
                output_type=pytesseract.Output.DICT, timeout=self.timeout_seconds,
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailableError("Tesseract executable not found") from exc
        except pytesseract.TesseractError as exc:  # subclass of RuntimeError: must come first
            if "language" in str(exc).lower():
                raise OcrUnavailableError("English Tesseract language data could not be loaded") from exc
            raise OcrError("Tesseract failed") from exc
        except RuntimeError as exc:
            # pytesseract signals its subprocess timeout with this RuntimeError message.
            if "timeout" in str(exc).lower():
                raise OcrTimeoutError("Tesseract exceeded the processing time limit") from exc
            raise OcrError("Tesseract failed") from exc
        duration_ms = round((time.perf_counter() - started) * 1000)
        lines = _group_lines(data, scale)
        words = [w for line in lines for w in line.words]
        mean = round(sum(w.confidence for w in words) / len(words), 1) if words else None
        return OcrOutput(
            raw_text=_reading_order_text(lines), lines=lines, mean_confidence=mean, word_count=len(words),
            engine_version=version, language=OCR_LANGUAGE, psm=self.psm, processing_scale=scale,
            preprocessing=steps, duration_ms=duration_ms,
        )
