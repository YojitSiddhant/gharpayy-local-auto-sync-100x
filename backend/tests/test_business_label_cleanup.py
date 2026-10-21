from types import SimpleNamespace

from backend.app.api.evidence import _business_label_cleanup


def line(text, left, top, right, bottom):
    return {"text": text, "confidence": 95, "bounds": {"left": left, "top": top, "right": right, "bottom": bottom}}


def row(message, lines):
    return SimpleNamespace(message_preview=message, source_lines=lines)


def test_removes_separate_spatial_business_label():
    value, uncertain = _business_label_cleanup(row("Need a private room TANIA", [
        line("Need a private room", 140, 200, 400, 225), line("TANIA", 140, 235, 220, 255)]))
    assert value == "Need a private room"
    assert uncertain is False


def test_removes_multiple_stacked_business_labels():
    value, uncertain = _business_label_cleanup(row("Visit scheduled tomorrow. TANIA NEW VISIT STAGE ANWESHA", [
        line("Visit scheduled tomorrow.", 140, 200, 400, 225),
        line("TANIA", 140, 235, 220, 255),
        line("NEW VISIT STAGE", 140, 265, 330, 285),
        line("ANWESHA", 140, 295, 235, 315),
    ]))
    assert value == "Visit scheduled tomorrow."
    assert uncertain is False


def test_preserves_single_line_legitimate_uppercase_message():
    value, uncertain = _business_label_cleanup(row("CALL ME TANIA", [line("CALL ME TANIA", 140, 200, 300, 225)]))
    assert value is None and uncertain is False


def test_ambiguous_geometry_is_unchanged():
    value, uncertain = _business_label_cleanup(row("Need a private room TANIA", [
        line("Need a private room", 140, 200, 400, 225), line("TANIA", 300, 235, 380, 255)]))
    assert value is None and uncertain is True
