from types import SimpleNamespace

from backend.app.api.evidence import _business_label_cleanup


def line(text, left, top, right, bottom, badge=False):
    value = {"text": text, "confidence": 95, "bounds": {"left": left, "top": top, "right": right, "bottom": bottom}}
    if badge:
        value["business_label_candidate"] = True
    return value


def merged_line(text, left, top, right, bottom, badge_words):
    value = line(text, left, top, right, bottom)
    value["words"] = [{"text": word, "bounds": {"left": left + index * 40, "top": top, "right": left + (index + 1) * 40, "bottom": bottom}}
                       for index, word in enumerate(text.split())]
    value["business_label_words"] = badge_words
    return value


def row(message, lines):
    return SimpleNamespace(message_preview=message, source_lines=lines)


def test_removes_separate_spatial_business_label():
    value, uncertain = _business_label_cleanup(row("Need a private room TANIA", [
        line("Need a private room", 140, 200, 400, 225), line("TANIA", 140, 235, 220, 255, True)]))
    assert value == "Need a private room"
    assert uncertain is False


def test_removes_multiple_stacked_business_labels():
    value, uncertain = _business_label_cleanup(row("Visit scheduled tomorrow. TANIA NEW VISIT STAGE ANWESHA", [
        line("Visit scheduled tomorrow.", 140, 200, 400, 225),
        line("TANIA", 140, 235, 220, 255, True),
        line("NEW VISIT STAGE", 140, 265, 330, 285, True),
        line("ANWESHA", 140, 295, 235, 315, True),
    ]))
    assert value == "Visit scheduled tomorrow."
    assert uncertain is False


def test_preserves_single_line_legitimate_uppercase_message():
    value, uncertain = _business_label_cleanup(row("CALL ME TANIA", [line("CALL ME TANIA", 140, 200, 300, 225)]))
    assert value is None and uncertain is False


def test_ambiguous_geometry_is_unchanged():
    value, uncertain = _business_label_cleanup(row("Need a private room TANIA", [
        line("Need a private room", 140, 200, 400, 225), line("TANIA", 300, 235, 380, 255, True)]))
    assert value is None and uncertain is True


def test_merged_line_removes_only_visual_badge_word_suffix():
    value, uncertain = _business_label_cleanup(row(
        "Are you available tomorrow? TANIA NEW VISIT STAGE",
        [merged_line("Are you available tomorrow? TANIA NEW VISIT STAGE", 140, 200, 560, 225,
                     ["TANIA", "NEW", "VISIT", "STAGE"])],
    ))
    assert value == "Are you available tomorrow?"
    assert uncertain is False


def test_merged_line_without_visual_words_is_unchanged():
    value, uncertain = _business_label_cleanup(row(
        "ARE YOU AVAILABLE TANIA", [line("ARE YOU AVAILABLE TANIA", 140, 200, 400, 225)]))
    assert value is None and uncertain is False
