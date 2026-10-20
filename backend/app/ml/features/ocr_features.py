"""Stable numeric feature vectors built from existing OCR/parser objects."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .geometry_features import association_features, box_features, distance_features
from .text_features import candidate_text_features

FEATURE_SCHEMA_VERSION = "phase7c-features-v1"


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _words(line: Any) -> list[Any]:
    words = getattr(line, "words", None)
    return list(words) if words else []


def extract_line_features(line: Any) -> dict[str, float]:
    words = _words(line)
    confidences = [_finite(getattr(word, "confidence", None)) for word in words]
    text = getattr(line, "text", "") or ""
    return {
        "line_word_count": float(len(words)),
        "line_character_count": float(len(text)),
        "line_mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "line_min_confidence": min(confidences) if confidences else 0.0,
        "line_max_confidence": max(confidences) if confidences else 0.0,
        "line_missing_confidence": float(not words or any(not hasattr(word, "confidence") for word in words)),
        "line_missing_coordinates": float(not words or any(not all(hasattr(word, key) for key in ("left", "top", "right", "bottom")) for word in words)),
    }


def extract_document_features(lines: list[Any] | None, *, width: int, height: int) -> dict[str, float]:
    """Aggregate OCR-only features; empty input remains distinguishable."""
    values = list(lines or [])
    word_count = sum(len(_words(line)) for line in values)
    char_count = sum(len(getattr(line, "text", "") or "") for line in values)
    confidences = [_finite(getattr(word, "confidence", None)) for line in values for word in _words(line)]
    return {
        "document_line_count": float(len(values)),
        "document_word_count": float(word_count),
        "document_character_count": float(char_count),
        "document_mean_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "document_missing_confidence": float(not confidences),
        "document_missing_coordinates": float(any(extract_line_features(line)["line_missing_coordinates"] for line in values)),
        "document_empty": float(not values),
        "document_width": _finite(width),
        "document_height": _finite(height),
    }


def _row_lines(row: Any) -> list[dict]:
    source = getattr(row, "source_lines", None) or []
    return [line for line in source if isinstance(line, dict)]


def _candidate_boxes(row: Any) -> tuple[Any, Any, Any]:
    lines = _row_lines(row)
    timestamp = getattr(row, "visible_timestamp_text", None)
    timestamp_line = next((line for line in lines if timestamp and line.get("text", "").strip() == timestamp.strip()), None)
    header = getattr(row, "header_bounds", None)
    header_line = header
    message_line = next((line.get("bounds") for line in lines if line is not timestamp_line and line.get("bounds") != header), None)
    return header_line, message_line, timestamp_line.get("bounds") if timestamp_line else None


@dataclass(frozen=True)
class FeatureVector:
    schema_version: str
    names: tuple[str, ...]
    values: tuple[float, ...]
    fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "fingerprint": self.fingerprint, "names": list(self.names), "values": list(self.values)}

    def __post_init__(self) -> None:
        if len(self.names) != len(self.values) or any(not math.isfinite(value) for value in self.values):
            raise ValueError("feature vector must contain finite values matching its schema")


def _schema_names() -> tuple[str, ...]:
    names = set(extract_document_features([], width=1, height=1))
    names.update(f"row_{key}" for key in box_features(None, 1, 1))
    names.update(f"header_{key}" for key in box_features(None, 1, 1))
    names.update(f"message_{key}" for key in box_features(None, 1, 1))
    names.update(f"timestamp_{key}" for key in box_features(None, 1, 1))
    names.update(f"text_{key}" for key in candidate_text_features(phone=None, message=None, timestamp=None))
    names.update(association_features(row=None, header=None, message=None, timestamp=None, width=1, height=1))
    names.update(distance_features(candidate=None, header=None, width=1, height=1))
    names.update({"row_source_line_count", "row_mean_line_confidence", "row_missing_confidence", "row_warning_count", "row_quarantined"})
    return tuple(sorted(names))


FEATURE_NAMES = _schema_names()


def _schema_fingerprint() -> str:
    schema = {"version": FEATURE_SCHEMA_VERSION, "names": FEATURE_NAMES, "dtype": "float64", "missing_value": 0.0}
    fingerprint = hashlib.sha256(json.dumps(schema, separators=(",", ":")).encode("utf-8")).hexdigest()
    return fingerprint


def _make_vector(values: dict[str, float]) -> FeatureVector:
    safe_values = tuple(_finite(values.get(name, 0.0)) for name in FEATURE_NAMES)
    return FeatureVector(FEATURE_SCHEMA_VERSION, FEATURE_NAMES, safe_values, _schema_fingerprint())


def feature_schema_fingerprint() -> str:
    return _schema_fingerprint()


def extract_row_features(*, row: Any, lines: list[Any] | None, width: int, height: int) -> FeatureVector:
    """Build a deterministic vector for one parser row.

    The serialized vector contains only numeric values. Raw OCR strings are
    used transiently to derive shape features and are never returned.
    """
    values = extract_document_features(lines, width=width, height=height)
    source_lines = _row_lines(row)
    confidences = [_finite(line.get("confidence")) for line in source_lines if "confidence" in line]
    header_box, message_box, timestamp_box = _candidate_boxes(row)
    values.update({
        "row_source_line_count": float(len(source_lines)),
        "row_mean_line_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "row_missing_confidence": float(not confidences),
        "row_warning_count": float(len(getattr(row, "warnings", None) or [])),
        "row_quarantined": float(bool(getattr(row, "quarantined", False))),
    })
    values.update({f"row_{key}": number for key, number in box_features(getattr(row, "row_bounds", None), width, height).items()})
    values.update({f"header_{key}": number for key, number in box_features(header_box, width, height).items()})
    values.update({f"message_{key}": number for key, number in box_features(message_box, width, height).items()})
    values.update({f"timestamp_{key}": number for key, number in box_features(timestamp_box, width, height).items()})
    values.update({f"text_{key}": number for key, number in candidate_text_features(phone=getattr(row, "phone_normalized", None), message=getattr(row, "message_preview", None), timestamp=getattr(row, "visible_timestamp_text", None), truncated=bool(getattr(row, "preview_truncated", False))).items()})
    values.update(association_features(row=getattr(row, "row_bounds", None), header=header_box, message=message_box, timestamp=timestamp_box, width=width, height=height))
    values.update(distance_features(candidate=timestamp_box, header=header_box, width=width, height=height))
    return _make_vector(values)

