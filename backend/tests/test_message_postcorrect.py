from backend.app.core.config import Settings
from backend.app.ocr.message_postcorrect import correct_message


def test_corrects_only_one_known_confusion():
    assert correct_message("Need a discused update")[0] == "Need a discussed update"


def test_valid_words_and_ambiguous_candidates_are_unchanged():
    assert correct_message("still available")[0] == "still available"
    assert correct_message("reached")[0] == "reached"


def test_numbers_timestamps_and_hindi_are_unchanged():
    text = "Call +91 98765 43210 at 2:30 pm नमस्ते"
    assert correct_message(text) == (text, [])


def test_mode_defaults_off_and_accepts_only_three_values():
    assert Settings(_env_file=None).message_postcorrect_mode == "off"
    assert Settings(_env_file=None, message_postcorrect_mode="shadow").message_postcorrect_mode == "shadow"
