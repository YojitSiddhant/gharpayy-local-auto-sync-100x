"""Evidence storage consistency check (database ↔ private screenshot files).

Dry run by default; reports counts only (no file names, hashes, text or account labels):

    backend/.venv/bin/python -m backend.app.ocr.storage_check
    backend/.venv/bin/python -m backend.app.ocr.storage_check --remove-orphans

`--remove-orphans` deletes ONLY files inside the configured evidence directory that no database record
references (and interrupted `.incoming-*` temp files older than an hour). It never deletes or edits database
rows, never follows symlinks, and re-checks references immediately before each deletion.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.ocr import ScreenshotCapture
from backend.app.ocr.storage import KEY_RE, TEMP_PREFIX

STALE_TEMP_SECONDS = 3600


@dataclass
class StorageReport:
    referenced_present: int = 0
    missing_files: int = 0          # DB says retained, file absent
    orphan_files: list[Path] = field(default_factory=list)  # evidence-named files no DB row references
    stale_temp_files: list[Path] = field(default_factory=list)
    unexpected_entries: int = 0     # anything else (other names, directories, symlinks) — never touched

    def summary(self) -> dict[str, int]:
        return {"referenced_present": self.referenced_present, "missing_files": self.missing_files,
                "orphan_files": len(self.orphan_files), "stale_temp_files": len(self.stale_temp_files),
                "unexpected_entries": self.unexpected_entries}


def _referenced_keys(db: Session) -> set[str]:
    return {k for k in db.scalars(select(ScreenshotCapture.evidence_storage_key).where(ScreenshotCapture.evidence_storage_key.is_not(None)))}


def check_storage(db: Session, root: str | Path, now: float | None = None) -> StorageReport:
    report = StorageReport()
    directory = Path(root).resolve()
    referenced = _referenced_keys(db)
    present: set[str] = set()
    if directory.is_dir():
        current = time.time() if now is None else now
        for entry in directory.iterdir():
            if entry.is_symlink() or not entry.is_file():
                report.unexpected_entries += 1
            elif KEY_RE.fullmatch(entry.name):
                present.add(entry.name)
                if entry.name not in referenced:
                    report.orphan_files.append(entry)
            elif entry.name.startswith(TEMP_PREFIX):
                if current - entry.stat().st_mtime > STALE_TEMP_SECONDS:
                    report.stale_temp_files.append(entry)
            else:
                report.unexpected_entries += 1
    report.referenced_present = len(referenced & present)
    report.missing_files = len(referenced - present)
    return report


def remove_orphans(db: Session, root: str | Path, report: StorageReport) -> int:
    directory = Path(root).resolve()
    removed = 0
    for path in [*report.orphan_files, *report.stale_temp_files]:
        if path.parent != directory or path.is_symlink() or not path.is_file():
            continue  # only plain files directly inside the evidence directory
        if KEY_RE.fullmatch(path.name) and path.name in _referenced_keys(db):
            continue  # became referenced since the scan (e.g. a new upload); keep it
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    from backend.app.core.config import get_settings
    from backend.app.db.session import SessionLocal

    parser = argparse.ArgumentParser(description="Check evidence files against the database (dry run by default).")
    parser.add_argument("--remove-orphans", action="store_true", help="Delete unreferenced evidence files and stale temp files.")
    args = parser.parse_args(argv)
    root = get_settings().screenshot_storage_dir
    with SessionLocal() as db:
        report = check_storage(db, root)
        print(f"Storage check: {report.summary()}")
        if report.missing_files:
            print("Some records reference screenshots that are missing on disk; those readings keep their OCR text but cannot show an image or Retry OCR.")
        if args.remove_orphans:
            print(f"Removed {remove_orphans(db, root, report)} unreferenced file(s). Database records were not changed.")
        elif report.orphan_files or report.stale_temp_files:
            print("Re-run with --remove-orphans to delete only the unreferenced files listed above (counts only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
