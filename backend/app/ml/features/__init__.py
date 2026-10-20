"""Deterministic, numeric feature extraction for future local classifiers."""

from .ocr_features import (
    FEATURE_SCHEMA_VERSION,
    FeatureVector,
    extract_document_features,
    extract_line_features,
    extract_row_features,
    feature_schema_fingerprint,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "FeatureVector",
    "extract_document_features",
    "extract_line_features",
    "extract_row_features",
    "feature_schema_fingerprint",
]

