from types import SimpleNamespace

from PIL import Image, ImageDraw

from backend.app.ocr.business_badges import mark_business_badges


def badge_image(regions):
    image = Image.new("RGB", (800, 220), (22, 22, 22))
    draw = ImageDraw.Draw(image)
    for left, top, right, bottom, color in regions:
        draw.rounded_rectangle((left, top, right, bottom), radius=6, fill=color)
    return image


def row_for(lines):
    return SimpleNamespace(source_lines=lines)


def line(text, bounds):
    return {"text": text, "bounds": dict(zip(("left", "top", "right", "bottom"), bounds))}


def test_opencv_marks_arbitrary_single_badge_without_name_dictionary():
    image = badge_image([(140, 100, 250, 132, (126, 45, 160))])
    row = row_for([line("UNSEEN LABEL", (150, 108, 238, 125))])

    mark_business_badges(image, [row])

    assert row.source_lines[0]["business_label_candidate"] is True


def test_opencv_marks_multiple_adjacent_differently_colored_badges():
    image = badge_image([
        (140, 100, 285, 132, (80, 115, 35)),
        (294, 100, 385, 132, (125, 35, 95)),
        (394, 100, 450, 132, (25, 95, 135)),
        (459, 100, 560, 132, (145, 45, 25)),
    ])
    rows = row_for([
        line("NEW VISIT STAGE", (150, 108, 275, 125)),
        line("TANIA", (304, 108, 375, 125)),
        line("CRM", (404, 108, 442, 125)),
        line("RE-INITIATE", (469, 108, 550, 125)),
    ])

    mark_business_badges(image, [rows])

    assert sum(item.get("business_label_candidate", False) for item in rows.source_lines) == 4


def test_colored_unrelated_region_without_ocr_overlap_is_not_marked():
    image = badge_image([(30, 30, 110, 95, (180, 25, 90))])
    row = row_for([line("CUSTOMER MESSAGE", (160, 108, 320, 125))])

    mark_business_badges(image, [row])

    assert "business_label_candidate" not in row.source_lines[0]


def test_merged_line_marks_only_words_overlapping_badge_region():
    image = badge_image([(300, 100, 480, 132, (125, 35, 95))])
    row = row_for([{
        "text": "Message VISUAL LABEL",
        "bounds": {"left": 150, "top": 108, "right": 470, "bottom": 125},
        "words": [
            {"text": "Message", "bounds": {"left": 150, "top": 108, "right": 285, "bottom": 125}},
            {"text": "VISUAL", "bounds": {"left": 305, "top": 108, "right": 370, "bottom": 125}},
            {"text": "LABEL", "bounds": {"left": 378, "top": 108, "right": 450, "bottom": 125}},
        ],
    }])

    mark_business_badges(image, [row])

    assert row.source_lines[0]["business_label_words"] == ["VISUAL", "LABEL"]
