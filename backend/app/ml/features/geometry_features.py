"""Resolution-independent numeric geometry and association features."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


def _value(item: Any, key: str, default: float = 0.0) -> float:
    value = item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _box(item: Any) -> tuple[float, float, float, float] | None:
    if item is None:
        return None
    if isinstance(item, Mapping) and "bounds" in item:
        item = item["bounds"]
    values = tuple(_value(item, key, float("nan")) for key in ("left", "top", "right", "bottom"))
    if not all(math.isfinite(v) for v in values) or values[2] < values[0] or values[3] < values[1]:
        return None
    return values  # type: ignore[return-value]


def _overlap(a: tuple[float, float, float, float] | None, b: tuple[float, float, float, float] | None) -> float:
    if not a or not b:
        return 0.0
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = width * height
    smaller = min(max(0.0, (a[2] - a[0]) * (a[3] - a[1])), max(0.0, (b[2] - b[0]) * (b[3] - b[1])))
    return area / smaller if smaller else 0.0


def box_features(box: Any, width: int, height: int) -> dict[str, float]:
    """Return finite normalized bounds; missing boxes are explicit indicators."""
    left, top, right, bottom = _box(box) or (0.0, 0.0, 0.0, 0.0)
    w, h = max(1.0, float(width)), max(1.0, float(height))
    return {
        "box_present": float(_box(box) is not None),
        "box_left_norm": left / w,
        "box_top_norm": top / h,
        "box_right_norm": right / w,
        "box_bottom_norm": bottom / h,
        "box_width_norm": max(0.0, right - left) / w,
        "box_height_norm": max(0.0, bottom - top) / h,
        "box_center_x_norm": ((left + right) / 2) / w,
        "box_center_y_norm": ((top + bottom) / 2) / h,
    }


def association_features(*, row: Any, header: Any, message: Any, timestamp: Any, width: int, height: int) -> dict[str, float]:
    """Describe whether candidate boxes occupy the same row without text content."""
    row_box, header_box = _box(row), _box(header)
    message_box, timestamp_box = _box(message), _box(timestamp)
    boxes = [box for box in (header_box, message_box, timestamp_box) if box is not None]
    centers = [((box[1] + box[3]) / 2) for box in boxes]
    row_top, row_bottom = (row_box[1], row_box[3]) if row_box else (0.0, 0.0)
    result = {
        "association_header_row_overlap": _overlap(header_box, row_box),
        "association_message_row_overlap": _overlap(message_box, row_box),
        "association_timestamp_row_overlap": _overlap(timestamp_box, row_box),
        "association_all_candidates_present": float(len(boxes) == 3),
        "association_same_row_vertical_alignment": 0.0,
        "association_adjacent_row_indicator": 0.0,
        "association_header_timestamp_gap_norm": 0.0,
        "association_header_message_gap_norm": 0.0,
    }
    if centers:
        result["association_same_row_vertical_alignment"] = 1.0 - min(1.0, (max(centers) - min(centers)) / max(1.0, float(height)))
    if header_box and timestamp_box:
        result["association_header_timestamp_gap_norm"] = abs(((timestamp_box[1] + timestamp_box[3]) / 2) - ((header_box[1] + header_box[3]) / 2)) / max(1.0, float(height))
    if header_box and message_box:
        result["association_header_message_gap_norm"] = abs(((message_box[1] + message_box[3]) / 2) - ((header_box[1] + header_box[3]) / 2)) / max(1.0, float(height))
    if row_box and boxes and (min(centers) < row_top or max(centers) > row_bottom):
        result["association_adjacent_row_indicator"] = 1.0
    return result


def distance_features(*, candidate: Any, header: Any, width: int, height: int) -> dict[str, float]:
    a, b = _box(candidate), _box(header)
    if not a or not b:
        return {"candidate_header_distance_x_norm": 0.0, "candidate_header_distance_y_norm": 0.0, "candidate_header_missing_geometry": 1.0}
    return {
        "candidate_header_distance_x_norm": abs(((a[0] + a[2]) / 2) - ((b[0] + b[2]) / 2)) / max(1.0, float(width)),
        "candidate_header_distance_y_norm": abs(((a[1] + a[3]) / 2) - ((b[1] + b[3]) / 2)) / max(1.0, float(height)),
        "candidate_header_missing_geometry": 0.0,
    }

