"""Explicit, reversible evidence retention.

Adapted from the reference's logical-delete retention: old evidence is moved to Trash (audited,
restorable); permanent deletion stays a separate, explicit "Purge trash" action. Disabled by default
(EVIDENCE_RETENTION_DAYS=0) and never runs on its own — an operator runs:

    backend/.venv/bin/python -m backend.app.ocr.retention            # dry run: counts only
    backend/.venv/bin/python -m backend.app.ocr.retention --apply    # move to Trash

Output contains counts only — no screenshot content, OCR text, phones or account labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.ocr import ScreenshotCapture, utcnow
from backend.app.ocr.evidence import set_trash, ui_status

RETENTION_REVIEWER = "Retention policy"


def retention_candidates(db: Session, days: int, now: datetime | None = None) -> list[ScreenshotCapture]:
    """Active (not trashed), not in-flight evidence observed more than `days` days ago."""
    if days <= 0:
        return []
    cutoff = (now or utcnow()) - timedelta(days=days)
    observed = func.coalesce(ScreenshotCapture.captured_at, ScreenshotCapture.received_at)
    return list(db.scalars(select(ScreenshotCapture).where(
        ScreenshotCapture.trashed_at.is_(None), ScreenshotCapture.status != "processing", observed < cutoff)))


def plan(db: Session, days: int, now: datetime | None = None) -> dict[str, int]:
    counts: dict[str, int] = {"total": 0}
    for capture in retention_candidates(db, days, now):
        status = ui_status(capture)
        counts[status] = counts.get(status, 0) + 1
        counts["total"] += 1
    return counts


def apply(db: Session, days: int, now: datetime | None = None) -> int:
    candidates = retention_candidates(db, days, now)
    for capture in candidates:
        set_trash(db, capture, f"Retention: observed more than {days} days ago", RETENTION_REVIEWER, restore=False)
    return len(candidates)


def main(argv: list[str] | None = None) -> int:
    from backend.app.core.config import get_settings
    from backend.app.db.session import SessionLocal

    parser = argparse.ArgumentParser(description="Move evidence older than the retention window to Trash (reversible).")
    parser.add_argument("--days", type=int, default=None, help="Override EVIDENCE_RETENTION_DAYS for this run.")
    parser.add_argument("--apply", action="store_true", help="Actually move evidence to Trash. Without it, only counts are shown.")
    args = parser.parse_args(argv)
    days = args.days if args.days is not None else get_settings().evidence_retention_days
    if days <= 0:
        print("Retention disabled (EVIDENCE_RETENTION_DAYS=0). Nothing to do.")
        return 0
    with SessionLocal() as db:
        if not args.apply:
            counts = plan(db, days)
            print(f"Dry run: {counts['total']} evidence item(s) older than {days} days would move to Trash "
                  f"(by status: {({k: v for k, v in counts.items() if k != 'total'})}). Re-run with --apply.")
            return 0
        moved = apply(db, days)
        print(f"Moved {moved} evidence item(s) older than {days} days to Trash. Restore or purge them from the page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
