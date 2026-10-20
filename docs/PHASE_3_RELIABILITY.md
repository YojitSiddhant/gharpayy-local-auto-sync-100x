# Phase 3 — Feature-gap audit and reliability improvements

No UI redesign, no new services, no schema migration. The page layout and controls are unchanged.

## OCR parsing (`backend/app/ocr/whatsapp_parser.py`, parser `phase3-chatlist-0.2`)

**Weakness found:** when a header and its timestamp are close, Tesseract returns one line
(`+91 98765 43210 2:52pm`). Neither a phone header nor a right-aligned timestamp could anchor the row, so it was lost.
On a synthetic 3-row screenshot with tight spacing, only 1 of 3 rows was recovered.

**Change:** `split_trailing_timestamps()` splits the trailing one or two words off a line when they alone form a
visible timestamp, using that line's own Tesseract word boxes (no characters changed or invented; lines without word
geometry are untouched). A split timestamp may anchor a row only when the text before it is a phone-like header,
so times inside message previews never create rows. Affected rows carry a warning. Raw OCR text is unchanged.
Same synthetic screenshot: 3 of 3 rows. This is a synthetic regression test, **not** an accuracy measurement.

## Backend reliability

- Any unexpected error during OCR/parsing is recorded as a failed attempt (`OCR_FAILED`), so a capture can never be
  left in `processing`; Retry OCR works on it.
- Captures orphaned in `processing` past the stale window (e.g. server restart mid-OCR) are marked
  `OCR_INTERRUPTED` (retryable) when the evidence list loads. Idempotent; deletes nothing.
- Duplicate hit counter uses an atomic SQL increment.
- Upload responses include `capture.trashed`; a duplicate of trashed evidence is explained in the page.

## Retention (`backend/app/ocr/retention.py`)

Off by default (`EVIDENCE_RETENTION_DAYS=0`). Never runs on its own. Explicit operator command:

```bash
backend/.venv/bin/python -m backend.app.ocr.retention            # dry run: counts only
backend/.venv/bin/python -m backend.app.ocr.retention --apply    # move old evidence to Trash
backend/.venv/bin/python -m backend.app.ocr.retention --days 30  # override the window for one run
```

It only moves active evidence older than the window to Trash (reason `Retention: …`, reviewer `Retention policy`,
audited, restorable). Permanent deletion still requires **Purge trash… → Confirm permanent purge** on the page.
Output contains counts only.

## Capture and queue (`src/lib/capture.ts`, panel)

- Queue failures: only file-specific rejections (corrupt, wrong type, too large…) are dropped; OCR timeouts/failures
  are recorded server-side and point to Retry OCR; **everything else keeps the screenshot and pauses the queue**
  (previously e.g. `OCR_ENGINE_UNAVAILABLE`, `LOCAL_TOKEN_REQUIRED`, unreadable responses dropped the screenshot).
- Exact-pixel change gate (reference `edge-change-gate.js`): a frame byte-identical to the previous one is skipped
  before upload and counted as a same-account duplicate. No perceptual threshold.
- Sampling ticks come from a Worker timer (falls back to `setInterval`) to reduce Chrome background-tab throttling.
- Capture gap > 15 s between ticks (reference capture policy) stops sampling and requires confirming the crop again.
- Muted / ended source tracks (minimized, hidden, locked) are not sampled; a message is shown until frames return.
- Source size change: sampling stops, the new size is adopted, the crop resets to the full source, and sampling
  resumes only after **Confirm crop & retain frames** (previously confirmation could never succeed again).
- Waits for the shared video to report its size after `play()` (avoids a 0 × 0 source).
- Cancelled/denied permission shows a clear message; capture stays stopped.
- Corrections typed just before reload/close are saved with a `keepalive` request on `pagehide`.

## Not manually verified

Background-tab behaviour of the Worker ticker, muted-track behaviour of real windows, and the merged-line split on real
WhatsApp screenshots need the planned manual WhatsApp capture session.
