from backend.app.ocr.whatsapp_parser import normalize_timestamp_text, parse_chat_list
from backend.tests.conftest import make_line


def test_reference_timestamp_cleanup_is_field_specific_and_does_not_repair_digits():
    assert normalize_timestamp_text(". 10:24 am,") == "10:24 am"
    assert normalize_timestamp_text("10:24 an") == "10:24 am"
    assert normalize_timestamp_text("10:2O am") == "10:2O am"


def test_timestamp_cleanup_preserves_row_association_and_preview_text():
    result = parse_chat_list([
        make_line("+91 98765 43210", 140, 180),
        make_line(". 2:52 pm,", 560, 184),
        make_line("Need a private room", 140, 228),
    ], 720, 1280, field_cleanup=True)
    assert len(result.rows) == 1
    assert result.rows[0].visible_timestamp_text == "2:52 pm"
    assert result.rows[0].message_preview == "Need a private room"
    assert result.rows[0].phone_normalized == "+919876543210"
