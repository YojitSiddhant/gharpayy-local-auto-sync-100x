"""Privacy-safe, deterministic metrics for Phase 7A.

Inputs are already-reviewed values or compact prediction records. Missing,
unreadable, and ambiguous labels are never silently counted as correct.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_PHONE = re.compile(r"^\+91[6-9]\d{9}$")


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _eligible_truth(value: object, status: str | None = None) -> str | None:
    if status in {"not_visible", "unreadable", "ambiguous", "missing", "partial"}:
        return None
    return _clean(value)


def exact_match_accuracy(predictions: Sequence[object], truths: Sequence[object], *, statuses: Sequence[str | None] | None = None) -> float:
    """Exact-match accuracy over eligible, verified truths; zero for no denominator."""
    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have equal length")
    statuses = statuses or [None] * len(truths)
    if len(statuses) != len(truths):
        raise ValueError("statuses must match truths length")
    eligible = [_eligible_truth(t, s) for t, s in zip(truths, statuses)]
    pairs = [(p, t) for p, t in zip(predictions, eligible) if t is not None]
    return sum(_clean(p) == t for p, t in pairs) / len(pairs) if pairs else 0.0


def precision_recall(predictions: Iterable[object], truths: Iterable[object], *, valid_truth: callable | None = None) -> dict[str, float]:
    """Binary precision/recall for extracted candidates.

    A missing prediction is a false negative when a valid truth exists. A
    prediction with no valid truth is a false positive.
    """
    preds, refs = list(predictions), list(truths)
    if len(preds) != len(refs):
        raise ValueError("predictions and truths must have equal length")
    is_valid = valid_truth or (lambda value: _clean(value) is not None)
    tp = sum(_clean(p) is not None and is_valid(t) and _clean(p) == _clean(t) for p, t in zip(preds, refs))
    fp = sum(_clean(p) is not None and (not is_valid(t) or _clean(p) != _clean(t)) for p, t in zip(preds, refs))
    fn = sum(_clean(p) is None and is_valid(t) for p, t in zip(preds, refs))
    return {"precision": tp / (tp + fp) if tp + fp else 0.0, "recall": tp / (tp + fn) if tp + fn else 0.0}


def invalid_number_acceptance_rate(predictions: Sequence[object]) -> float:
    """Rate of non-empty predictions that are not valid normalized Indian mobiles."""
    accepted = [_clean(p) for p in predictions if _clean(p) is not None]
    return sum(not bool(_PHONE.fullmatch(p or "")) for p in accepted) / len(accepted) if accepted else 0.0


def timestamp_exact_match_accuracy(predictions: Sequence[object], truths: Sequence[object], *, statuses: Sequence[str | None] | None = None) -> float:
    return exact_match_accuracy(predictions, truths, statuses=statuses)


def _levenshtein(left: Sequence[object], right: Sequence[object]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def character_error_rate(predictions: Sequence[str | None], truths: Sequence[str | None], *, statuses: Sequence[str | None] | None = None) -> float:
    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have equal length")
    statuses = statuses or [None] * len(truths)
    refs = [_eligible_truth(t, s) for t, s in zip(truths, statuses)]
    pairs = [(p or "", t) for p, t in zip(predictions, refs) if t is not None]
    total_ref = sum(len(t) for _, t in pairs)
    return sum(_levenshtein(p, t) for p, t in pairs) / total_ref if total_ref else 0.0


def word_error_rate(predictions: Sequence[str | None], truths: Sequence[str | None], *, statuses: Sequence[str | None] | None = None) -> float:
    if len(predictions) != len(truths):
        raise ValueError("predictions and truths must have equal length")
    statuses = statuses or [None] * len(truths)
    refs = [_eligible_truth(t, s) for t, s in zip(truths, statuses)]
    pairs = [(p or "", t) for p, t in zip(predictions, refs) if t is not None]
    total_ref = sum(len(t.split()) for _, t in pairs)
    return sum(_levenshtein(p.split(), t.split()) for p, t in pairs) / total_ref if total_ref else 0.0


def row_detection_metrics(predicted_rows: Iterable[object], truth_rows: Iterable[object]) -> dict[str, float]:
    predicted, truth = set(predicted_rows), set(truth_rows)
    tp = len(predicted & truth)
    return {"precision": tp / len(predicted) if predicted else 0.0, "recall": tp / len(truth) if truth else 0.0}


def association_accuracy(predictions: Sequence[object], truths: Sequence[object], *, statuses: Sequence[str | None] | None = None) -> float:
    return exact_match_accuracy(predictions, truths, statuses=statuses)


def field_to_row_association_accuracy(predicted_rows: Sequence[object], truth_rows: Sequence[object]) -> float:
    return exact_match_accuracy(predicted_rows, truth_rows)


def wrong_assignment_rate(predicted_rows: Sequence[object], truth_rows: Sequence[object]) -> float:
    if len(predicted_rows) != len(truth_rows):
        raise ValueError("predicted_rows and truth_rows must have equal length")
    return sum(_clean(p) is not None and _clean(p) != _clean(t) for p, t in zip(predicted_rows, truth_rows)) / len(truth_rows) if truth_rows else 0.0


def review_rate(statuses: Sequence[str]) -> float:
    return sum(s in {"review", "abstain", "needs_review"} for s in statuses) / len(statuses) if statuses else 0.0

