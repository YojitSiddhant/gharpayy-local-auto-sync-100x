"""Conservative WhatsApp chat-list row parser over OCR lines.

Pure logic: no OCR, no database, no I/O. Output is unverified OCR evidence.

Rules (adapted from the lead-loved-flow and Gharpayy_Local_Sync_FINAL references):
- Only a header line that is *entirely* a phone number can yield a phone candidate.
  Phones mentioned in message previews never become identities.
- Digits are never repaired (O→0, l→1 …) and never invented; ambiguous or partial
  phone headers stay unverified and are quarantined.
- Timestamps are kept as visible text only; nothing is inferred when absent.
- Sender direction is never inferred.
- Display names are never used to merge identities. Conflicts are quarantined.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Protocol

PARSER_VERSION = "phase6-chatlist-0.3"

PHONE_STATUS_CANDIDATE = "candidate"
PHONE_STATUS_AMBIGUOUS = "ambiguous"
PHONE_STATUS_CONFLICT = "conflict"
PHONE_STATUS_NONE = "none"

TIME_FULL_RE = re.compile(
    r"^(?:(?:1[0-2]|0?[1-9])[:.][0-5]\d\s*(?:am|pm)|(?:[01]?\d|2[0-3]):[0-5]\d|yesterday|today|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))$",
    re.I,
)
# A strictly formatted Indian mobile header: +91 then exactly ten digits starting 6-9.
STRICT_IN_HEADER_RE = re.compile(r"^\+91[\s-]*([6-9](?:[\s-]*\d){9})$")
# A bare ten-digit Indian mobile (country code not visible).
BARE_IN_HEADER_RE = re.compile(r"^([6-9](?:[\s-]*\d){9})$")
# International E.164-style visible headers: preserve the observed country code and
# normalize only whitespace/separators. This remains a candidate only when the whole
# header is phone-like; names and message numbers never become identities.
INTERNATIONAL_HEADER_RE = re.compile(r"^\+(\d[\d\s().-]{6,18}\d)$")
# Anything that *looks* like a phone header, including OCR-confusable letters. Used only to flag ambiguity.
PHONE_LIKE_RE = re.compile(r"^\+?[\dOoIl|()\s.-]{8,}$")
PHONE_IN_TEXT_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
# Avatar slivers / status dots that OCR attaches to a header edge.
EDGE_ARTIFACTS_RE = re.compile(r"^[\s@©®●•○|>]+|[\s@©®●•○|>]+$")
NAV_WORDS_RE = re.compile(r"^(?:chats|calls|updates|communities|tools|status|whatsapp|search|ask meta ai)\b", re.I)
BADGE_RE = re.compile(r"^\W*\d{1,3}\+?\W*$")
TRUNCATED_RE = re.compile(r"(?:\.{2,}|…)\s*$")

MAX_HEADER_CHARS = 512
MAX_PREVIEW_CHARS = 2000


class LineLike(Protocol):
    text: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class SplitLine:
    """A segment of one OCR line, rebuilt only from that line's own words (text, boxes, confidences)."""
    text: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int
    split_timestamp: bool = False
    # True when the text before a split timestamp is a phone-like header; only then may it anchor a row.
    head_is_phone: bool = False


@dataclass
class PhoneEvidence:
    raw: str | None
    normalized: str | None
    status: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedRow:
    position: int
    display_header_text: str | None
    phone_raw: str | None
    phone_normalized: str | None
    phone_status: str
    message_preview: str | None
    preview_truncated: bool
    visible_timestamp_text: str | None
    timestamp_kind: str | None
    row_bounds: dict[str, int]
    header_bounds: dict[str, int] | None
    ocr_confidence: float | None
    warnings: list[str]
    quarantined: bool
    source_lines: list[dict]
    identity_status: str = "unverified"
    review_status: str = "needs_review"
    sender_direction: str = "unknown"


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    warnings: list[str]
    parsed_region: dict[str, int]
    parser_version: str = PARSER_VERSION


def timestamp_kind(text: str) -> str | None:
    t = text.strip().lower()
    if not TIME_FULL_RE.fullmatch(t):
        return None
    if t in ("yesterday", "today"):
        return "relative_word"
    if "/" in t:
        return "date"
    if t.endswith("day"):
        return "weekday"
    return "clock_time"


def classify_phone_header(header_text: str | None) -> PhoneEvidence:
    """Classify a header line. Never repairs or invents digits."""
    if not header_text:
        return PhoneEvidence(None, None, PHONE_STATUS_NONE)
    text = EDGE_ARTIFACTS_RE.sub("", header_text).strip()
    if text.count("+") > 1:
        return PhoneEvidence(text, None, PHONE_STATUS_CONFLICT, ["Header contains more than one country-coded number; association quarantined"])
    strict = STRICT_IN_HEADER_RE.fullmatch(text)
    if strict:
        return PhoneEvidence(text, "+91" + re.sub(r"\D", "", strict.group(1)), PHONE_STATUS_CANDIDATE)
    if BARE_IN_HEADER_RE.fullmatch(text):
        return PhoneEvidence(text, None, PHONE_STATUS_AMBIGUOUS, ["Country code not visible; number not normalized"])
    if text.startswith("+91"):
        return PhoneEvidence(text, None, PHONE_STATUS_AMBIGUOUS, ["Indian phone header is incomplete or malformed; digits not repaired"])
    international = INTERNATIONAL_HEADER_RE.fullmatch(text)
    if international:
        digits = "+" + re.sub(r"\D", "", international.group(1))
        if 8 <= len(digits) - 1 <= 15:
            return PhoneEvidence(text, digits, PHONE_STATUS_CANDIDATE)
        return PhoneEvidence(text, None, PHONE_STATUS_AMBIGUOUS, ["International phone header length is outside the supported visible range"])
    if PHONE_LIKE_RE.fullmatch(text):
        if re.search(r"[OoIl|]", text):
            reason = "Phone-like header contains OCR-confusable characters; digits not repaired"
        elif text.startswith("+") and not text.startswith("+91"):
            reason = "Non-Indian or unsupported number format; not normalized"
        else:
            reason = "Partial or malformed phone header; not normalized"
        return PhoneEvidence(text, None, PHONE_STATUS_AMBIGUOUS, [reason])
    warnings = []
    if PHONE_IN_TEXT_RE.search(text):
        warnings.append("Header mixes a name and phone-like digits; digits not treated as identity")
    return PhoneEvidence(None, None, PHONE_STATUS_NONE, warnings)


def _segment(words: list, split_timestamp: bool, head_is_phone: bool = False) -> SplitLine:
    return SplitLine(
        text=" ".join(w.text for w in words), confidence=round(sum(w.confidence for w in words) / len(words), 1),
        left=min(w.left for w in words), top=min(w.top for w in words),
        right=max(w.right for w in words), bottom=max(w.bottom for w in words), split_timestamp=split_timestamp,
        head_is_phone=head_is_phone,
    )


def split_trailing_timestamps(lines: list[LineLike]) -> list[LineLike]:
    """Tesseract may merge a header and its timestamp into one line ("+91 98765 43210 2:52pm").
    Split the trailing one or two words off when they alone form a visible timestamp. Uses the
    line's own word boxes; text is never altered. Lines without word geometry are left as they are."""
    out: list[LineLike] = []
    for line in lines:
        words = list(getattr(line, "words", None) or [])
        split = None
        for tail in (2, 1):
            if len(words) > tail and TIME_FULL_RE.fullmatch(" ".join(w.text for w in words[-tail:]).strip()):
                split = tail
                break
        if split is None:
            out.append(line)
            continue
        head = _segment(words[:-split], False)
        out.extend([head, _segment(words[-split:], True, _is_phone_header(head.text))])
    return out


def _bounds(line: LineLike) -> dict[str, int]:
    return {"left": line.left, "top": line.top, "right": line.right, "bottom": line.bottom}


def _is_phone_header(text: str) -> bool:
    return classify_phone_header(text).status != PHONE_STATUS_NONE


def parse_chat_list(lines: list[LineLike], width: int, height: int) -> ParseResult:
    warnings = ["Unverified OCR evidence; rows are not a complete chat-list coverage claim."]
    if width <= 0 or height <= 0:
        return ParseResult([], warnings + ["Image has no usable area"], {"left": 0, "top": 0, "right": 0, "bottom": 0})

    region_right = width
    if width > height * 1.15:
        # Desktop / WhatsApp Web: the chat list is the left panel (reference default 42%).
        region_right = round(width * 0.42)
        warnings.append("Landscape screenshot: only the left 42% was parsed as the chat list; rows may be incomplete.")
    nav_cutoff = height * 0.92 if height > width * 1.15 else height
    region = {"left": 0, "top": 0, "right": region_right, "bottom": round(nav_cutoff)}

    usable = [
        l for l in lines
        if l.text.strip() and l.left < region_right
        and not (l.top > nav_cutoff and (BADGE_RE.match(l.text) or NAV_WORDS_RE.match(l.text.strip())))
    ]
    usable = split_trailing_timestamps([l for l in usable if l.top <= nav_cutoff])
    if not usable:
        return ParseResult([], warnings + ["No chat-list rows recognized; raw OCR text retained for manual review."], region)

    line_height = max(8.0, statistics.median(l.bottom - l.top for l in usable))
    w = region_right

    anchors: list[tuple[int, str]] = []
    for line in usable:
        text = line.text.strip()
        if (line.left > w * 0.6 or getattr(line, "head_is_phone", False)) and TIME_FULL_RE.fullmatch(text):
            anchors.append((line.top, "time"))
        elif w * 0.04 < line.left < w * 0.74 and _is_phone_header(text):
            anchors.append((line.top, "phone"))
    anchors.sort()
    compact: list[list] = []
    for y, source in anchors:
        if compact and y - compact[-1][0] <= line_height * 1.2:
            if source == "time":
                compact[-1][0] = y
        else:
            compact.append([y, source])

    rows: list[ParsedRow] = []
    for idx, (y, _source) in enumerate(compact):
        next_y = compact[idx + 1][0] if idx + 1 < len(compact) else nav_cutoff
        near = [l for l in usable if y - line_height * 0.8 <= l.top < next_y - line_height * 0.5]
        timestamps = [
            l for l in near
            if (l.left > w * 0.6 or getattr(l, "split_timestamp", False)) and abs(l.top - y) <= line_height * 0.9
            and TIME_FULL_RE.fullmatch(l.text.strip())
        ]
        timestamp_line = min(timestamps, key=lambda l: abs(l.top - y)) if timestamps else None
        header_parts = sorted(
            (l for l in near
             if l is not timestamp_line and w * 0.04 < l.left < w * 0.76 and abs(l.top - y) <= line_height * 0.9
             and not TIME_FULL_RE.fullmatch(l.text.strip())),
            key=lambda l: l.left,
        )
        header_text = " ".join(l.text.strip() for l in header_parts) or None
        if header_text and NAV_WORDS_RE.match(header_text):
            continue
        header_ids = {id(l) for l in header_parts}
        header_bottom = max((l.bottom for l in header_parts), default=y + line_height)
        preview_lines = sorted(
            (l for l in near
             if id(l) not in header_ids and l is not timestamp_line
             and l.top >= header_bottom - line_height * 0.3 and l.top < min(next_y - line_height * 0.3, y + line_height * 4)
             and w * 0.04 < l.left < w * 0.95
             and not (l.left > w * 0.82 and BADGE_RE.match(l.text))
             and not NAV_WORDS_RE.match(l.text.strip())),
            key=lambda l: (l.top, l.left),
        )
        preview = " ".join(l.text.strip() for l in preview_lines) or None

        row_warnings: list[str] = []
        phone = classify_phone_header(header_text)
        row_warnings.extend(phone.warnings)
        if header_text and len(header_text) > MAX_HEADER_CHARS:
            header_text = header_text[:MAX_HEADER_CHARS]
            row_warnings.append("Header text truncated for storage")
        if preview and len(preview) > MAX_PREVIEW_CHARS:
            preview = preview[:MAX_PREVIEW_CHARS]
            row_warnings.append("Preview text truncated for storage")
        if preview and PHONE_IN_TEXT_RE.search(preview):
            row_warnings.append("Phone number appears in message preview; not used as contact identity")
        if header_text is None:
            row_warnings.append("Contact header not captured")
        if timestamp_line is None:
            row_warnings.append("Timestamp not captured")
        elif getattr(timestamp_line, "split_timestamp", False):
            row_warnings.append("Timestamp was separated from a merged OCR header line using word boxes")
        if preview is None:
            row_warnings.append("Message preview not captured")

        quality = [l.confidence for l in [*header_parts, *preview_lines]]
        confidence = round(sum(quality) / len(quality) / 100, 3) if quality else None
        if confidence is not None and confidence < 0.75:
            row_warnings.append("Low OCR confidence")
        all_lines = [*header_parts, *([timestamp_line] if timestamp_line else []), *preview_lines]
        top = min((l.top for l in all_lines), default=y)
        bottom = max((l.bottom for l in all_lines), default=y)
        header_box = None
        if header_parts:
            header_box = {
                "left": min(l.left for l in header_parts), "top": min(l.top for l in header_parts),
                "right": max(l.right for l in header_parts), "bottom": max(l.bottom for l in header_parts),
            }
        rows.append(ParsedRow(
            position=len(rows) + 1,
            display_header_text=header_text,
            phone_raw=phone.raw, phone_normalized=phone.normalized, phone_status=phone.status,
            message_preview=preview,
            preview_truncated=bool(preview and TRUNCATED_RE.search(preview)),
            visible_timestamp_text=timestamp_line.text.strip() if timestamp_line else None,
            timestamp_kind=timestamp_kind(timestamp_line.text) if timestamp_line else None,
            row_bounds={"left": 0, "top": top, "right": region_right, "bottom": bottom},
            header_bounds=header_box,
            ocr_confidence=confidence,
            warnings=row_warnings,
            quarantined=phone.status in (PHONE_STATUS_AMBIGUOUS, PHONE_STATUS_CONFLICT),
            source_lines=[{"text": l.text, "confidence": l.confidence, "bounds": _bounds(l)} for l in sorted(all_lines, key=lambda l: (l.top, l.left))],
        ))

    _quarantine_cross_row_conflicts(rows)
    if not rows:
        warnings.append("No chat-list rows recognized; raw OCR text retained for manual review.")
    return ParseResult(rows, warnings, region)


def _quarantine_cross_row_conflicts(rows: list[ParsedRow]) -> None:
    by_phone: dict[str, list[ParsedRow]] = {}
    by_name: dict[str, list[ParsedRow]] = {}
    for row in rows:
        if row.phone_normalized:
            by_phone.setdefault(row.phone_normalized, []).append(row)
        elif row.display_header_text and row.phone_status == PHONE_STATUS_NONE:
            by_name.setdefault(row.display_header_text.strip().casefold(), []).append(row)
    for group in by_phone.values():
        if len(group) > 1:
            for row in group:
                row.quarantined = True
                row.warnings.append("Same phone header appears in more than one row of this screenshot; association quarantined")
    for group in by_name.values():
        if len(group) > 1:
            for row in group:
                row.quarantined = True
                row.warnings.append("Display name repeats in another row; names are never used to merge identities")
