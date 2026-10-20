"""Conservative, project-owned OCR message postcorrection.

This intentionally uses a small reviewed vocabulary rather than importing the
reference project's private dictionary. It is advisory unless explicitly
enabled and never handles phone or timestamp fields.
"""
from __future__ import annotations

import re

_WORDS = frozenset({
    "available", "discussed", "domlur", "flat", "office", "prebook",
    "private", "room", "still", "this", "whatsapp", "yesterday",
})
_CONFUSIONS = {frozenset(("l", "i")), frozenset(("o", "c")),
               frozenset(("e", "c")), frozenset(("a", "o"))}
_TOKEN = re.compile(r"[A-Za-z]{4,}")


def _distance_one(a: str, b: str) -> bool:
    if len(a) != len(b):
        return abs(len(a) - len(b)) == 1 and _edit_detail(a, b) is not None
    return sum(x != y for x, y in zip(a, b)) == 1


def _edit_detail(a: str, b: str) -> tuple[str, str, str] | None:
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        return ("sub", diffs[0][0], diffs[0][1]) if len(diffs) == 1 else None
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    for index, char in enumerate(longer):
        if longer[:index] + longer[index + 1:] == shorter:
            return ("delete", char, "")
    return None


def _known_confusion(original: str, candidate: str) -> bool:
    detail = _edit_detail(original, candidate)
    if detail is None:
        return False
    if detail[0] == "sub":
        return frozenset((detail[1], detail[2])) in _CONFUSIONS
    # Only allow a doubled-letter collapse, e.g. "stilll" -> "still".
    longer = original if len(original) > len(candidate) else candidate
    return any(longer[i] == longer[i - 1] for i in range(1, len(longer)))


def correct_message(text: str | None) -> tuple[str | None, list[dict[str, str]]]:
    """Return corrected text and auditable changes; never raises."""
    if not text:
        return text, []
    changes: list[dict[str, str]] = []
    parts: list[str] = []
    cursor = 0
    for match in _TOKEN.finditer(text):
        parts.append(text[cursor:match.start()])
        original = match.group(0)
        lower = original.lower()
        candidates = [word for word in _WORDS if _distance_one(lower, word) and _known_confusion(lower, word)]
        if len(candidates) == 1 and lower not in _WORDS:
            replacement = candidates[0]
            parts.append(replacement if original.islower() else replacement.capitalize())
            changes.append({"original": original, "corrected": replacement, "reason": "known_ocr_confusion"})
        else:
            parts.append(original)
        cursor = match.end()
    parts.append(text[cursor:])
    corrected = "".join(parts)
    return corrected, changes
