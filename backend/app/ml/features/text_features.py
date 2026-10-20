"""Numeric text-shape features only; raw text is never returned."""
from __future__ import annotations

import re
from collections.abc import Mapping

from backend.app.ocr.whatsapp_parser import TIME_FULL_RE

_CONFUSABLES = set("OoIl|B")
_INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")


def _safe_text(text: str | None) -> str:
    return text if isinstance(text, str) else ""


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def text_shape_features(text: str | None, *, truncated: bool = False) -> dict[str, float]:
    """Return fixed numeric shape signals, never a text identifier."""
    value = _safe_text(text)
    length = len(value)
    digits = sum(ch.isdigit() for ch in value)
    whitespace = sum(ch.isspace() for ch in value)
    punctuation = sum(not ch.isalnum() and not ch.isspace() for ch in value)
    normalized = re.sub(r"[\s-]", "", value)
    timestamp = bool(TIME_FULL_RE.fullmatch(value.strip()))
    lower = value.strip().lower()
    return {
        "text_present": float(bool(value.strip())),
        "text_length": float(length),
        "digit_count": float(digits),
        "digit_ratio": _ratio(digits, length),
        "whitespace_ratio": _ratio(whitespace, length),
        "punctuation_ratio": _ratio(punctuation, length),
        "has_visible_plus91": float(value.strip().startswith("+91")),
        "indian_mobile_format_valid": float(bool(_INDIAN_MOBILE.fullmatch(normalized))),
        "has_ocr_confusable": float(any(ch in _CONFUSABLES for ch in value)),
        "timestamp_format_valid": float(timestamp),
        "timestamp_clock_time": float(timestamp and (":" in lower or "." in lower) and lower[-2:] in {"am", "pm"}),
        "timestamp_relative_word": float(lower in {"today", "yesterday"}),
        "timestamp_weekday": float(lower in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}),
        "timestamp_date": float(timestamp and "/" in lower),
        "message_truncated": float(truncated or value.rstrip().endswith(("...", "…"))),
    }


def timestamp_shape_features(text: str | None) -> dict[str, float]:
    return {f"timestamp_{key}": value for key, value in text_shape_features(text).items() if key.startswith("timestamp_")}


def candidate_text_features(*, phone: str | None, message: str | None, timestamp: str | None, truncated: bool = False) -> dict[str, float]:
    result: dict[str, float] = {}
    for prefix, value, is_truncated in (("phone", phone, False), ("message", message, truncated), ("timestamp", timestamp, False)):
        result.update({f"{prefix}_{key}": number for key, number in text_shape_features(value, truncated=is_truncated).items()})
    return result

