from backend.app.ocr.whatsapp_parser import classify_phone_header, parse_chat_list, timestamp_kind
from backend.app.ocr.engine import OcrLine, OcrWord
from backend.tests.conftest import fake_chat_lines, make_line

W, H = 720, 1280


def test_strict_header_phone_becomes_unverified_candidate():
    result = parse_chat_list(fake_chat_lines(), W, H)
    first = result.rows[0]
    assert first.phone_status == "candidate"
    assert first.phone_normalized == "+919876543210"
    assert first.visible_timestamp_text == "2:52 pm"
    assert first.message_preview == "Need a private room"
    assert first.identity_status == "unverified" and first.review_status == "needs_review"
    assert first.sender_direction == "unknown"
    assert first.header_bounds == {"left": 140, "top": 180, "right": 140 + 16 * 15, "bottom": 210}


def test_phone_in_message_text_is_never_an_identity():
    second = parse_chat_list(fake_chat_lines(), W, H).rows[1]
    assert second.display_header_text == "Akhil Kumar"
    assert second.phone_status == "none" and second.phone_normalized is None
    assert any("not used as contact identity" in w for w in second.warnings)


def test_preview_only_phone_lines_never_create_rows():
    lines = [make_line("Call +91 98765 43210 tomorrow", 140, 200), make_line("My phone is +91 94491 90529", 140, 260)]
    assert parse_chat_list(lines, W, H).rows == []


def test_ambiguous_header_is_quarantined_without_repairing_digits():
    lines = [make_line("+91 9876O 43210", 140, 180), make_line("3:00 pm", 560, 184), make_line("hello", 140, 228)]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.phone_status == "ambiguous" and row.quarantined
    assert row.phone_normalized is None
    assert row.phone_raw == "+91 9876O 43210"


def test_header_with_two_numbers_is_a_conflict():
    evidence = classify_phone_header("+91 98765 43210 +91 94491 90529")
    assert evidence.status == "conflict" and evidence.normalized is None


def test_partial_and_foreign_headers_stay_unverified():
    assert classify_phone_header("+91 98765 4321").status == "ambiguous"
    assert classify_phone_header("98765 43210").status == "ambiguous"
    assert classify_phone_header("98765 43210").normalized is None
    assert classify_phone_header("+44 7911 123456").status == "candidate"
    assert classify_phone_header("+44 7911 123456").normalized == "+447911123456"
    assert classify_phone_header("+91 12345 12345").status == "ambiguous"
    assert classify_phone_header("Rahul").status == "none"


def test_header_edge_artifacts_are_stripped_but_digits_untouched():
    assert classify_phone_header("@ +91 98765 43210 ●").normalized == "+919876543210"


def test_missing_timestamp_is_not_invented():
    lines = [make_line("+91 98765 43210", 140, 180), make_line("Chats Calls Updates", 140, 228)]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.visible_timestamp_text is None and row.timestamp_kind is None
    assert row.message_preview is None
    assert "Timestamp not captured" in row.warnings


def test_same_phone_in_two_rows_is_quarantined():
    lines = [make_line("+91 98765 43210", 140, 180), make_line("2:52 pm", 560, 184),
             make_line("+91 98765 43210", 140, 400), make_line("2:40 pm", 560, 404)]
    rows = parse_chat_list(lines, W, H).rows
    assert len(rows) == 2 and all(r.quarantined for r in rows)


def test_repeated_display_name_never_merges_rows():
    lines = [make_line("Alex", 140, 180), make_line("2:52 pm", 560, 184), make_line("hi", 140, 228),
             make_line("Alex", 140, 400), make_line("2:40 pm", 560, 404), make_line("hello", 140, 448)]
    rows = parse_chat_list(lines, W, H).rows
    assert len(rows) == 2
    assert all(r.quarantined and any("never used to merge" in w for w in r.warnings) for r in rows)


def test_truncated_preview_flag_and_raw_text_preserved():
    lines = [make_line("+91 98765 43210", 140, 180), make_line("10:24 am", 560, 184), make_line("Thankyou...", 140, 228)]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.preview_truncated and row.message_preview == "Thankyou..."
    assert [s["text"] for s in row.source_lines] == ["+91 98765 43210", "10:24 am", "Thankyou..."]


def test_navigation_badges_in_bottom_band_are_ignored():
    lines = [make_line("99+", 300, 1240), make_line("Chats", 100, 1240)]
    assert parse_chat_list(lines, W, H).rows == []


def test_no_rows_produces_warning():
    result = parse_chat_list([], W, H)
    assert result.rows == [] and any("No chat-list rows" in w for w in result.warnings)


def test_timestamp_kinds():
    assert timestamp_kind("2:52 pm") == "clock_time"
    assert timestamp_kind("Yesterday") == "relative_word"
    assert timestamp_kind("Monday") == "weekday"
    assert timestamp_kind("13/09/26") == "date"
    assert timestamp_kind("pen") is None


def test_status_tick_like_token_is_removed_only_with_icon_geometry():
    lines = [
        make_line("+91 98765 43210", 140, 180),
        make_line("2:52 pm", 560, 184),
        OcrLine(
            text="W Need a room", confidence=90, left=140, top=228, right=380, bottom=245,
            words=[
                OcrWord("W", 90, 140, 228, 167, 245),
                OcrWord("Need", 90, 175, 228, 225, 245),
                OcrWord("a", 90, 232, 228, 245, 245),
                OcrWord("room", 90, 252, 228, 310, 245),
            ],
        ),
    ]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.message_preview == "Need a room"
    assert any("status glyph" in warning for warning in row.warnings)


def test_legitimate_single_letter_message_is_preserved():
    lines = [make_line("+91 98765 43210", 140, 180), make_line("2:52 pm", 560, 184)]
    lines.append(
        OcrLine(
            text="W hello", confidence=90, left=140, top=228, right=250, bottom=258,
            words=[OcrWord("W", 90, 140, 228, 156, 258), OcrWord("hello", 90, 164, 228, 244, 258)],
        )
    )
    assert parse_chat_list(lines, W, H).rows[0].message_preview == "W hello"


def test_reaction_preview_is_read_in_visual_order_without_inventing_emoji():
    lines = [
        make_line("+91 883 065 6029", 140, 180), make_line("11:12 am", 560, 184),
        make_line('to: "VISIT SLOT BOOKED"', 408, 228), make_line("Reacted", 140, 229),
    ]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.message_preview == 'Reacted to: "VISIT SLOT BOOKED"'


def test_reaction_text_with_ocr_captured_emoji_is_preserved_as_text():
    lines = [
        make_line("+91 883 065 6029", 140, 180), make_line("11:12 am", 560, 184),
        make_line('Reacted 👍', 140, 229), make_line('to: "VISIT SLOT BOOKED"', 408, 228),
    ]
    row = parse_chat_list(lines, W, H).rows[0]
    assert row.message_preview == 'Reacted 👍 to: "VISIT SLOT BOOKED"'
