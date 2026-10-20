# Reference-inspired OCR demo

Implemented in the current project only. The reference project remains read-only.

## What changed

The parser now applies conservative, field-specific timestamp cleanup inspired by:

- `Gharpayy_Local_Sync_FINAL/gharpayy_ocr/layout_segment.py`
- `Gharpayy_Local_Sync_FINAL/gharpayy_ocr/vision.py`
- `Gharpayy_Local_Sync_FINAL/gharpayy_ocr/timestamp_parse.py`

It removes punctuation attached to timestamp crop edges and accepts Tesseract's
common `an` reading of `am`. It does not repair digits, alter message previews,
or change phone identity rules. Existing row geometry and source lines remain intact.

## Configuration

`LAYOUT_OCR_MODE` is available as `off`, `shadow`, or `on`:

- `off` (default): existing behavior.
- `shadow`: reserved comparison mode; persisted OCR remains unchanged.
- `on`: enables the conservative timestamp candidate cleanup.

For a local demo, set it in the backend environment before starting FastAPI:

```bash
LAYOUT_OCR_MODE=on backend/.venv/bin/python -m uvicorn backend.app.main:app --reload
```

Do not load reference `.joblib` files or enable ML for this demo.

## Verification

Focused tests cover punctuation cleanup, the `an`/`am` OCR variant, no digit
repair, row association, phone preservation and message-preview preservation.
They use synthetic fixtures. No human-verified real WhatsApp ground truth was
available, so this report does not claim an accuracy percentage or real-world lift.
