"""Bounded, privacy-safe OCR pipeline benchmark (synthetic screenshots only).

    backend/.venv/bin/python -m backend.benchmarks.ocr_benchmark                 # 20 images, OCR pipeline
    backend/.venv/bin/python -m backend.benchmarks.ocr_benchmark --count 40 --db  # + MySQL timings (rolled back)

Measures, per synthetic image: validation (signature + decode limits), SHA-256, Tesseract OCR, parsing;
reports average / p50 / p95 / max, failures, duplicate share, Python peak allocation and process max RSS.
`--db` times persistence (capture + result + rows insert, list query, detail serialisation) inside ONE
transaction that is always rolled back — nothing is left in the database. Images are generated in memory;
no customer data, no files written. Results describe this machine and synthetic images only; they are not
real-WhatsApp throughput or accuracy figures.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import resource
import statistics
import sys
import time
import tracemalloc

from PIL import Image, ImageDraw, ImageFont

from backend.app.core.config import Settings
from backend.app.ocr.engine import TesseractEngine, tesseract_version
from backend.app.ocr.image_validation import ImageLimits, decode_rgb, validate_image
from backend.app.ocr.whatsapp_parser import parse_chat_list

MAX_COUNT = 200


def synthetic_screenshot(index: int) -> bytes:
    """Deterministic fictional chat list (portrait, 720×1280). Numbers are synthetic and vary by index."""
    image = Image.new("RGB", (720, 1280), "white")
    draw = ImageDraw.Draw(image)
    big, small = ImageFont.load_default(size=30), ImageFont.load_default(size=26)
    draw.text((40, 40), "Chats", fill="black", font=big)
    y = 180
    for row in range(6):
        n = (index * 7 + row * 13) % 10_000
        header = f"+91 98{n:04d} {(index + row) % 100_000:05d}" if row % 2 == 0 else f"Contact {index}-{row}"
        draw.ellipse((30, y - 5, 110, y + 75), fill="#cccccc")
        draw.text((140, y), header, fill="black", font=big)
        draw.text((560, y + 4), f"{1 + (row + index) % 12}:{(row * 7 + index) % 60:02d} pm", fill="#555555", font=small)
        draw.text((140, y + 48), f"Synthetic preview message {index}.{row}", fill="#444444", font=small)
        y += 170
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"avg_ms": round(statistics.fmean(values), 2), "p50_ms": round(statistics.median(values), 2), "p95_ms": round(p95, 2), "max_ms": round(ordered[-1], 2)}


def run(count: int, duplicate_every: int, with_db: bool) -> dict:
    settings = Settings(_env_file=None) if not with_db else Settings()
    limits = ImageLimits(settings.ocr_max_upload_bytes, settings.ocr_max_image_pixels, settings.ocr_max_image_side, settings.ocr_min_image_side)
    engine = TesseractEngine(settings.ocr_tesseract_timeout_seconds, settings.ocr_tesseract_psm)
    timings: dict[str, list[float]] = {k: [] for k in ("validate", "sha256", "ocr", "parse", "end_to_end_new_image", "end_to_end_duplicate")}
    seen: set[str] = set()
    duplicates = failures = rows_found = 0
    # Every Nth image repeats the previous image's exact bytes (exercises the duplicate path).
    sources = [i - 1 if duplicate_every and i > 0 and i % duplicate_every == 0 else i for i in range(count)]
    sources = [sources[i - 1] if duplicate_every and i > 0 and i % duplicate_every == 0 else s for i, s in enumerate(sources)]
    payloads = [synthetic_screenshot(s) for s in sources]
    tracemalloc.start()
    for data in payloads:
        start = time.perf_counter()
        try:
            t = time.perf_counter(); image = validate_image(data, "image/png", limits); timings["validate"].append((time.perf_counter() - t) * 1000)
            t = time.perf_counter(); digest = hashlib.sha256(data).hexdigest(); timings["sha256"].append((time.perf_counter() - t) * 1000)
            if digest in seen:  # exact duplicate: the service returns the stored result without OCR
                duplicates += 1
                timings["end_to_end_duplicate"].append((time.perf_counter() - start) * 1000)
                continue
            seen.add(digest)
            t = time.perf_counter(); output = engine.run(decode_rgb(image.data)); timings["ocr"].append((time.perf_counter() - t) * 1000)
            t = time.perf_counter(); parsed = parse_chat_list(output.lines, image.width, image.height); timings["parse"].append((time.perf_counter() - t) * 1000)
            rows_found += len(parsed.rows)
            timings["end_to_end_new_image"].append((time.perf_counter() - start) * 1000)
        except Exception:  # noqa: BLE001 - counted, never hidden
            failures += 1
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    new_images = timings["end_to_end_new_image"]
    mean_s = statistics.fmean(new_images) / 1000 if new_images else 0
    report = {
        "label": "MEASURED on synthetic screenshots on this machine (not real WhatsApp data)",
        "machine": {"python": platform.python_version(), "platform": platform.platform(), "tesseract": tesseract_version()},
        "images": count, "unique": len(seen), "exact_duplicates": duplicates, "failures": failures,
        "failure_rate": round(failures / count, 4) if count else 0, "rows_parsed": rows_found,
        "latency": {k: summarize(v) for k, v in timings.items()},
        "memory": {"python_heap_peak_mb_excludes_native_pillow_tesseract": round(peak / 1024 / 1024, 1),
                   "process_max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)},
        "derived_sequential_capacity": {
            "note": "DERIVED from mean new-image latency, not measured end-to-end: 1 OCR slot, synthetic images, excludes upload, network, browser capture and MySQL time",
            "screenshots_per_hour": round(3600 / mean_s) if mean_s else None,
        },
    }
    if with_db:
        report["database"] = db_timings(payloads[: min(count, 50)], settings, limits)
    return report


def db_timings(payloads: list[bytes], settings: Settings, limits: ImageLimits) -> dict:
    """Persistence timings inside a single transaction that is ALWAYS rolled back."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from backend.app.db.session import engine as db_engine
    from backend.app.models.ocr import OcrResult, OcrRowObservation, ScreenshotCapture
    from backend.app.ocr import evidence as ev

    writes, lists, details = [], [], []
    with db_engine.connect() as connection:
        outer = connection.begin()
        if connection.execute(text("SELECT DATABASE()")).scalar() is None:
            outer.rollback()
            return {"skipped": "no database selected"}
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            for i, data in enumerate(payloads):
                image = validate_image(data, "image/png", limits)
                t = time.perf_counter()
                capture = ScreenshotCapture(sha256=hashlib.sha256(data + str(i).encode()).hexdigest(), account_label="benchmark (rolled back)",
                                            mime_type="image/png", byte_size=len(data), width=image.width, height=image.height, status="completed")
                result = OcrResult(attempt=1, status="completed", tesseract_psm=11, raw_text="synthetic " * 50, lines=[{"text": "x"}] * 40)
                for p in range(6):
                    result.rows.append(OcrRowObservation(position=p + 1, phone_status="none", row_left=0, row_top=p, row_right=1, row_bottom=p + 1))
                capture.results.append(result)
                session.add(capture)
                session.commit()  # releases a savepoint; the outer transaction is still rolled back below
                writes.append((time.perf_counter() - t) * 1000)
                t = time.perf_counter(); ev.list_evidence(session, "", "all"); lists.append((time.perf_counter() - t) * 1000)
                t = time.perf_counter(); ev.serialize_detail(session, capture); details.append((time.perf_counter() - t) * 1000)
        finally:
            session.close()
            outer.rollback()
    return {"note": "measured inside one rolled-back transaction; nothing persisted", "records": len(payloads),
            "insert_capture_result_rows": summarize(writes), "list_query": summarize(lists), "detail_serialize": summarize(details)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=20, help=f"synthetic images (1–{MAX_COUNT})")
    parser.add_argument("--duplicate-every", type=int, default=5, help="every Nth image repeats the previous bytes (0 = none)")
    parser.add_argument("--db", action="store_true", help="also time MySQL writes/reads inside a rolled-back transaction")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= MAX_COUNT:
        parser.error(f"--count must be between 1 and {MAX_COUNT}")
    print(json.dumps(run(args.count, max(0, args.duplicate_every), args.db), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
