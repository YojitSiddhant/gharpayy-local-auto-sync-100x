"""Deterministic, independently labeled synthetic row candidates for Phase 7D."""
from __future__ import annotations

from dataclasses import dataclass

from backend.app.ocr.whatsapp_parser import ParsedRow


@dataclass(frozen=True)
class SyntheticCandidate:
    group_id: str
    example_id: str
    label: int
    row: ParsedRow
    width: int
    height: int


def _row(*, position: int, width: int, height: int, label: int, variant: int) -> ParsedRow:
    y = 120 + (variant % 4) * 160
    positive = label == 1
    header = {"left": round(width * 0.18), "top": y, "right": round(width * 0.58), "bottom": y + 28} if positive else None
    timestamp = "2:30 pm" if positive else None
    message = "Need a room near office" if positive else None
    source_lines = []
    if header:
        source_lines.append({"text": "+91 98765 43210", "confidence": 88.0 + variant % 8, "bounds": header})
    if positive:
        source_lines.append({"text": timestamp, "confidence": 84.0, "bounds": {"left": round(width * 0.78), "top": y + 3, "right": width - 20, "bottom": y + 28}})
        source_lines.append({"text": message, "confidence": 80.0, "bounds": {"left": round(width * 0.18), "top": y + 45, "right": round(width * 0.68), "bottom": y + 70}})
    return ParsedRow(
        position=position, display_header_text="+91 98765 43210" if positive else None,
        phone_raw="+91 98765 43210" if positive else None, phone_normalized="+919876543210" if positive else None,
        phone_status="candidate" if positive else "none", message_preview=message,
        preview_truncated=False, visible_timestamp_text=timestamp, timestamp_kind="clock_time" if positive else None,
        row_bounds={"left": 0, "top": y - 20, "right": width, "bottom": y + 90}, header_bounds=header,
        ocr_confidence=0.85 if positive else 0.0, warnings=[] if positive else ["Candidate is an unrelated interface region"],
        quarantined=False, source_lines=source_lines,
    )


def synthetic_candidates(group_count: int = 30) -> list[SyntheticCandidate]:
    """Return two independently labeled candidates per capture group."""
    candidates: list[SyntheticCandidate] = []
    for group in range(group_count):
        width, height = ((720, 1280) if group % 2 == 0 else (1080, 1920))
        candidates.append(SyntheticCandidate(f"synthetic-row-group-{group:03d}", f"positive-{group:03d}", 1, _row(position=1, width=width, height=height, label=1, variant=group), width, height))
        candidates.append(SyntheticCandidate(f"synthetic-row-group-{group:03d}", f"negative-{group:03d}", 0, _row(position=1, width=width, height=height, label=0, variant=group), width, height))
    return candidates

