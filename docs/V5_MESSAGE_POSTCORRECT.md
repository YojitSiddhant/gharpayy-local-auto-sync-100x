# V5 message postcorrection integration

This adapts the V5 technique from `gharpayy_ocr/postcorrect.py` without importing
its private dictionary or datasets. The current project uses a small explicitly
reviewed vocabulary and only corrects `message_preview` after OCR parsing.

Set `MESSAGE_POSTCORRECT_MODE` to:

- `off` (default): unchanged OCR output.
- `shadow`: unchanged saved message text; proposed corrections are recorded in
  the row warning trace for comparison.
- `on`: applies unambiguous known-confusion corrections while preserving raw OCR
  text in `OcrResult.raw_text` and `OcrResult.lines`.

No machine correction is marked human-verified or exported as an ML label.
Phones, timestamps, identity fields, digit-containing tokens and Hindi text are
not corrected. Tests use synthetic approved fixtures only; no real-world accuracy
gain is claimed.
