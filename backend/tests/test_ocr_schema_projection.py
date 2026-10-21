from types import SimpleNamespace

from backend.app.schemas.ocr import row_out


def make_row(message="Raw OCR message", verifications=None):
    return SimpleNamespace(
        position=1, display_header_text="+919876543210", phone_candidate_raw="+91 98765 43210",
        phone_candidate_normalized="+919876543210", phone_status="candidate", message_preview=message,
        preview_truncated=False, visible_timestamp_text="2:52 pm", timestamp_kind="clock_time",
        row_left=0, row_top=10, row_right=600, row_bottom=90, header_bounds=None,
        ocr_confidence=0.95, warnings=[], quarantined=False, identity_status="unverified",
        review_status="needs_review", sender_direction="unknown", observed_name=None,
        source_lines=[], field_verifications=verifications or [],
    )


def verification(status="verified", value="Clean message"):
    return SimpleNamespace(
        field_name="message", observed_value="Raw OCR message", verified_value=value,
        status=status, dataset_split="unassigned", approved_for_dataset=False,
        revision=1, reviewer="tester",
    )


def test_row_projection_uses_approved_verified_message_without_mutating_raw_value():
    row = make_row(verifications=[verification()])

    projected = row_out(row)

    assert projected.message_preview == "Clean message"
    assert row.message_preview == "Raw OCR message"
    assert projected.phone.normalized == "+919876543210"
    assert projected.visible_timestamp_text == "2:52 pm"


def test_row_projection_keeps_raw_message_without_verified_correction():
    row = make_row(verifications=[verification(status="incorrect", value="Do not use")])

    assert row_out(row).message_preview == "Raw OCR message"


def test_row_projection_keeps_raw_message_when_no_verification_exists():
    row = make_row()

    assert row_out(row).message_preview == "Raw OCR message"


def test_row_projection_removes_only_visual_label_suffix_without_persisting_correction():
    row = make_row(message="Please let us know TANIA")
    row.source_lines = [
        {"text": "Please let us know", "bounds": {"left": 10, "top": 10, "right": 200, "bottom": 30}},
        {"text": "TANIA", "bounds": {"left": 10, "top": 35, "right": 80, "bottom": 55}, "business_label_candidate": True},
    ]
    projected = row_out(row)
    assert projected.message_preview == "Please let us know"
    assert row.message_preview == "Please let us know TANIA"


def test_row_projection_preserves_uppercase_text_without_badge_marker():
    row = make_row(message="Please contact TANIA today")
    row.source_lines = [{"text": row.message_preview, "bounds": {"left": 10, "top": 10, "right": 240, "bottom": 30}}]
    assert row_out(row).message_preview == row.message_preview


def test_row_projection_returns_all_merged_badge_groups_as_separate_labels():
    row = make_row(message="Hi, got a stay? RE-INITIATE NOVEMBER CRM TANIA")
    row.source_lines = [{
        "text": row.message_preview,
        "bounds": {"left": 10, "top": 10, "right": 500, "bottom": 30},
        "business_label_candidate": True,
        "business_label_groups": ["RE-INITIATE", "NOVEMBER", "CRM", "TANIA"],
    }]

    projected = row_out(row)

    assert projected.message_preview == "Hi, got a stay?"
    assert projected.business_labels == ["RE-INITIATE", "NOVEMBER", "CRM", "TANIA"]


def test_same_name_badges_with_distinct_geometry_remain_separate():
    row = make_row(message="Message TANIA TANIA")
    row.source_lines = [
        {"text": "Message", "bounds": {"left": 10, "top": 10, "right": 180, "bottom": 30}},
        {"text": "TANIA", "bounds": {"left": 10, "top": 35, "right": 90, "bottom": 55}, "business_label_candidate": True},
        {"text": "TANIA", "bounds": {"left": 10, "top": 60, "right": 90, "bottom": 80}, "business_label_candidate": True},
    ]
    projected = row_out(row)
    assert projected.business_labels == ["TANIA", "TANIA"]


def test_projection_removes_good_lead_and_keeps_all_visual_badges_separate():
    row = make_row(message="S Check if location is feasible for you ? IMMEDIATE AI HANDOFF GOOD LEAD TANIA")
    row.source_lines = [
        {"text": "S Check if location is feasible for you ?", "bounds": {"left": 270, "top": 347, "right": 719, "bottom": 375}},
        {"text": "IMMEDIATE", "bounds": {"left": 539, "top": 395, "right": 643, "bottom": 415}, "business_label_candidate": True, "business_label_groups": ["IMMEDIATE"]},
        {"text": "AI HANDOFF", "bounds": {"left": 278, "top": 396, "right": 391, "bottom": 416}, "business_label_candidate": True, "business_label_groups": ["AI HANDOFF"]},
        {"text": "GOOD LEAD", "bounds": {"left": 415, "top": 397, "right": 517, "bottom": 414}, "business_label_candidate": True, "business_label_groups": ["GOOD LEAD"]},
        {"text": "TANIA", "bounds": {"left": 666, "top": 397, "right": 725, "bottom": 415}, "business_label_candidate": True, "business_label_groups": ["TANIA"]},
    ]

    projected = row_out(row)

    assert projected.message_preview == "S Check if location is feasible for you ?"
    assert projected.business_labels == ["IMMEDIATE", "AI HANDOFF", "GOOD LEAD", "TANIA"]
    assert row.message_preview.endswith("GOOD LEAD TANIA")


def test_api_projection_reorders_real_reaction_preview_without_adding_emoji():
    row = make_row(message='S to: "VISIT SLOT BOOKED" Reacted')
    row.source_lines = [
        {"text": 'to: "VISIT SLOT BOOKED"', "bounds": {"left": 408, "top": 518, "right": 912, "bottom": 546}},
        {"text": "Reacted", "bounds": {"left": 269, "top": 519, "right": 365, "bottom": 544}},
    ]
    assert row_out(row).message_preview == 'S Reacted to: "VISIT SLOT BOOKED"'
