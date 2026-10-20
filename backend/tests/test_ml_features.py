import math

from backend.app.ml.features import feature_schema_fingerprint
from backend.app.ml.features.geometry_features import association_features, box_features
from backend.app.ml.features.ocr_features import extract_document_features, extract_row_features
from backend.app.ml.features.text_features import candidate_text_features, text_shape_features
from backend.app.ocr.engine import OcrLine, OcrWord
from backend.app.ocr.whatsapp_parser import ParsedRow


def line(text: str, left: int, top: int, confidence: float = 90.0, with_words: bool = True) -> OcrLine:
    word = OcrWord(text=text, confidence=confidence, left=left, top=top, right=left + max(10, len(text) * 8), bottom=top + 20)
    return OcrLine(text=text, confidence=confidence, left=left, top=top, right=word.right, bottom=word.bottom, words=[word] if with_words else [])


def parsed_row() -> ParsedRow:
    return ParsedRow(
        position=1, display_header_text="+91 98765 43210", phone_raw="+91 98765 43210",
        phone_normalized="+919876543210", phone_status="candidate", message_preview="Need a room...",
        preview_truncated=True, visible_timestamp_text="Yesterday", timestamp_kind="relative_word",
        row_bounds={"left": 0, "top": 150, "right": 720, "bottom": 300},
        header_bounds={"left": 140, "top": 170, "right": 320, "bottom": 195},
        ocr_confidence=0.9, warnings=[], quarantined=False,
        source_lines=[
            {"text": "+91 98765 43210", "confidence": 90.0, "bounds": {"left": 140, "top": 170, "right": 320, "bottom": 195}},
            {"text": "Yesterday", "confidence": 88.0, "bounds": {"left": 560, "top": 174, "right": 650, "bottom": 195}},
            {"text": "Need a room...", "confidence": 80.0, "bounds": {"left": 140, "top": 218, "right": 300, "bottom": 240}},
        ],
    )


def test_text_features_cover_indian_phone_and_ambiguity_without_repairing():
    valid = text_shape_features("+91 98765 43210")
    ambiguous = text_shape_features("+91 98B65 43210")
    assert valid["has_visible_plus91"] == 1.0
    assert valid["indian_mobile_format_valid"] == 1.0
    assert ambiguous["indian_mobile_format_valid"] == 0.0
    assert ambiguous["has_ocr_confusable"] == 1.0
    assert valid["message_truncated"] == 0.0


def test_timestamp_and_message_shape_features_are_numeric():
    values = candidate_text_features(phone=None, message="hello...", timestamp="Monday", truncated=True)
    assert values["timestamp_timestamp_weekday"] == 1.0
    assert values["message_message_truncated"] == 1.0
    assert all(isinstance(value, float) and math.isfinite(value) for value in values.values())


def test_empty_ocr_and_missing_coordinates_are_explicit():
    empty = extract_document_features([], width=720, height=1280)
    missing = extract_document_features([line("text", 0, 0, with_words=False)], width=720, height=1280)
    assert empty["document_empty"] == 1.0
    assert empty["document_missing_confidence"] == 1.0
    assert missing["document_missing_coordinates"] == 1.0


def test_geometry_normalizes_across_resolutions_and_association_is_safe():
    first = box_features({"left": 10, "top": 20, "right": 110, "bottom": 120}, 720, 1280)
    second = box_features({"left": 20, "top": 40, "right": 220, "bottom": 240}, 1440, 2560)
    assert first["box_left_norm"] == second["box_left_norm"]
    features = association_features(row=parsed_row().row_bounds, header=parsed_row().header_bounds, message=None, timestamp=None, width=720, height=1280)
    assert features["association_header_row_overlap"] > 0
    assert features["association_all_candidates_present"] == 0.0


def test_row_vector_is_deterministic_versioned_finite_and_privacy_safe():
    row = parsed_row()
    first = extract_row_features(row=row, lines=[line("+91 98765 43210", 140, 170), line("Yesterday", 560, 174)], width=720, height=1280)
    second = extract_row_features(row=row, lines=[line("+91 98765 43210", 140, 170), line("Yesterday", 560, 174)], width=720, height=1280)
    assert first == second
    assert first.fingerprint == feature_schema_fingerprint()
    assert first.schema_version == "phase7c-features-v1"
    assert all(math.isfinite(value) for value in first.values)
    serialized = first.as_dict()
    assert "+91" not in str(serialized)
    assert "Need a room" not in str(serialized)

