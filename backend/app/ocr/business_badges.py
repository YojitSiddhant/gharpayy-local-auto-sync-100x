"""Local visual evidence for WhatsApp Business badge text.

This is deliberately conservative: OCR text is marked as a badge candidate only
when its box is substantially covered by a saturated, compact UI region.  The
marker is evidence for the review/cleanup path; it never changes raw OCR text.
"""
from __future__ import annotations

from typing import Any


def mark_business_badges(image: Any, rows: list[Any]) -> None:
    """Annotate parsed source lines in-place when OpenCV visual evidence agrees.

    OpenCV is optional.  A missing or failing detector leaves the OCR result
    untouched so normal PaddleOCR/Tesseract processing remains available.
    """
    try:
        import cv2
        import numpy as np

        array = np.asarray(image.convert("RGB"))
        hsv = cv2.cvtColor(array, cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(hsv, np.array((0, 55, 35)), np.array((179, 255, 245)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions: list[tuple[int, int, int, int]] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height
            if 12 <= width <= 360 and 7 <= height <= 72 and 120 <= area <= 24000:
                regions.append((x, y, x + width, y + height))
        if not regions:
            regions = []

        def neutral_region(bounds: dict[str, Any]) -> tuple[int, int, int, int] | None:
            """Find a compact dark neutral chip around an OCR line when saturation is absent."""
            try:
                left, top = int(bounds["left"]), int(bounds["top"])
                right, bottom = int(bounds["right"]), int(bounds["bottom"])
            except (KeyError, TypeError, ValueError):
                return None
            width, height = right - left, bottom - top
            if not (24 <= width <= 360 and 10 <= height <= 48):
                return None
            x1, y1 = max(0, left - 14), max(0, top - 14)
            x2, y2 = min(array.shape[1], right + 14), min(array.shape[0], bottom + 14)
            crop = array[y1:y2, x1:x2]
            if crop.size == 0:
                return None
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            if gray.shape[0] < 8 or gray.shape[1] < 16:
                return None
            inner = gray[8:-8, 8:-8]
            border = np.concatenate((gray[:6].ravel(), gray[-6:].ravel(), gray[:, :6].ravel(), gray[:, -6:].ravel()))
            inner_median, border_median = float(np.median(inner)), float(np.median(border))
            if inner_median < 55 and border_median - inner_median >= 2.5:
                return (x1, y1, x2, y2)
            return None
        for row in rows:
            for line in row.source_lines:
                bounds = line.get("bounds") or {}
                try:
                    left, top = int(bounds["left"]), int(bounds["top"])
                    right, bottom = int(bounds["right"]), int(bounds["bottom"])
                except (KeyError, TypeError, ValueError):
                    continue
                line_area = max(1, (right - left) * (bottom - top))
                matched_groups: list[str] = []
                matched_colors: list[dict[str, str]] = []
                line_regions = [region for region in regions if max(0, min(right, region[2]) - max(left, region[0])) * max(0, min(bottom, region[3]) - max(top, region[1])) / line_area >= 0.35]
                if not line_regions:
                    neutral = neutral_region(bounds)
                    if neutral:
                        line_regions = [neutral]
                for rx1, ry1, rx2, ry2 in sorted(line_regions, key=lambda region: (region[1], region[0])):
                    overlap = max(0, min(right, rx2) - max(left, rx1)) * max(0, min(bottom, ry2) - max(top, ry1))
                    if overlap / line_area >= 0.35:
                        line["business_label_candidate"] = True
                        line["badge_bounds"] = {"left": rx1, "top": ry1, "right": rx2, "bottom": ry2}
                    crop = array[max(0, ry1):ry2, max(0, rx1):rx2]
                    mean = crop.reshape(-1, 3).mean(axis=0).astype(int) if crop.size else np.array((128, 128, 128))
                    background = "#%02x%02x%02x" % tuple(int(v) for v in mean)
                    luminance = (int(mean[0]) * 299 + int(mean[1]) * 587 + int(mean[2]) * 114) / 1000
                    color = {"background": background, "foreground": "#111b21" if luminance > 150 else "#ffffff"}
                    words = line.get("words") or []
                    marked_words = []
                    for word in words:
                        wb = word.get("bounds") or {}
                        try:
                            wx1, wy1, wx2, wy2 = (int(wb[k]) for k in ("left", "top", "right", "bottom"))
                        except (KeyError, TypeError, ValueError):
                            continue
                        word_area = max(1, (wx2 - wx1) * (wy2 - wy1))
                        word_overlap = max(0, min(wx2, rx2) - max(wx1, rx1)) * max(0, min(wy2, ry2) - max(wy1, ry1))
                        if word_overlap / word_area >= 0.35:
                            marked_words.append(word.get("text", ""))
                    if marked_words:
                        matched_groups.append(" ".join(marked_words))
                        matched_colors.append(color)
                    elif overlap / line_area >= 0.35 and str(line.get("text") or "").strip():
                        matched_groups.append(str(line["text"]).strip())
                        matched_colors.append(color)
                if matched_groups:
                    line["business_label_candidate"] = True
                    line["business_label_words"] = [word for group in matched_groups for word in group.split()]
                    line["business_label_groups"] = matched_groups
                    line["business_label_colors"] = matched_colors
    except Exception:  # optional visual evidence must never break OCR
        return
