# Phase 2 — English OCR, MySQL persistence and the Local Auto Sync page

> **Corrective update:** `/` and `/local-auto-sync` now render one shared page that ports the reference
> `lead-loved-flow` `LocalAutoSyncPanel` (layout, controls, wording, capture lifecycle). Evidence persists in
> MySQL and is browsed through the protected evidence API below. See "Evidence review API" and "Share source capture".

Workflow: select a WhatsApp screenshot → validate → upload → local English Tesseract OCR → cautious chat-list row parsing → MySQL → review in `/local-auto-sync`.

Everything produced is **unverified, review-only OCR evidence**. No leads are created, CRM import is disabled, and no hosted AI or OCR service is called.

## Request path and security boundary

```
Browser (/local-auto-sync)
  └─ POST /api/ocr/extract            Next.js route handler (same origin)
       checks: loopback Host, Origin == Host, Sec-Fetch-Site same-origin,
               x-gharpayy-client header (forces CORS preflight), image type, 10 MB cap
       adds:   x-local-api-token (server-only LOCAL_API_TOKEN)
  └─ POST http://127.0.0.1:8000/api/v1/ocr/extract    FastAPI
       checks: loopback client, loopback Host, no foreign Origin, token (constant-time)
```

- `LOCAL_API_TOKEN` (≥ 32 chars) lives only in the git-ignored `.env`. It is never prefixed with `NEXT_PUBLIC_` and is absent from browser bundles. Both services fail closed (503) when it is missing.
- FastAPI CORS remains GET-only (for `/health`); CORS is not used as authentication.
- `npm run dev` binds Next.js and Uvicorn to `127.0.0.1`.

## API

`POST /api/v1/ocr/extract` — raw image bytes as the request body.

| Header | Required | Notes |
| --- | --- | --- |
| `Content-Type` | yes | `image/png`, `image/jpeg` or `image/webp` |
| `X-Account-Label` | yes | URI-encoded, ≤ 90 chars; the WhatsApp account label (unverified) |
| `X-Filename` | no | URI-encoded original file name |
| `X-Local-Api-Token` | yes | added by the Next.js proxy only |

Responses: `201` new result, `200` exact duplicate (stored result returned, OCR not re-run). Errors are always `{"error": {"code", "message", "details?"}}` with `Cache-Control: no-store`:
`EMPTY_UPLOAD 400`, `UPLOAD_TOO_LARGE 413`, `IMAGE_DIMENSIONS_TOO_LARGE 413`, `UNSUPPORTED_MEDIA_TYPE 415`, `INVALID_SIGNATURE 415`, `SIGNATURE_MISMATCH 415`, `CORRUPTED_IMAGE 422`, `IMAGE_TOO_SMALL 422`, `ANIMATED_IMAGE_NOT_SUPPORTED 422`, `CAPTURE_IN_PROGRESS 409`, `OCR_BUSY 429`, `OCR_FAILED 500`, `OCR_ENGINE_UNAVAILABLE 503`, `OCR_TIMEOUT 504`, plus the access codes `LOOPBACK_ONLY`, `HOST_NOT_ALLOWED`, `ORIGIN_NOT_ALLOWED` (403), `LOCAL_TOKEN_REQUIRED` (401), `LOCAL_TOKEN_NOT_CONFIGURED` (503).

## Evidence review API (migration `20261020_0002`)

Browser calls go to `/api/local/evidence/...` (Next.js, same-origin, allowlisted paths and query params only),
which forwards to FastAPI `/api/v1/evidence/...` with the server-only token. Same local-only checks as upload.

| Method & path | Purpose |
| --- | --- |
| `GET /evidence?search=&status=all\|review\|reviewed\|error\|trash` | List saved evidence (search: filename, label, account, original/corrected text, phone digits) |
| `GET /evidence/stats` | Real counts: awaiting review, reviewed, failed, trash, average OCR ms, records/bytes vs limits, duplicate uploads |
| `GET /evidence/{id}` | Detail: metadata, SHA-256, dimensions, crop, original OCR text, corrected text, corrections, trash history, rows, conflicts, content cues, proposals |
| `GET /evidence/{id}/image` | Screenshot bytes, authenticated, `no-store`, `nosniff`; never a public path |
| `PATCH /evidence/{id}` | Corrected text (appends to `ocr_text_corrections`), screenshot label, reviewer-observed names |
| `POST /evidence/{id}/review` | Mark reviewed (identity stays unverified) |
| `POST /evidence/{id}/trash` / `restore` | Mandatory reason; recorded in `evidence_audit_events` |
| `POST /evidence/{id}/retry` | Re-run OCR for a failed reading from stored evidence (new attempt; original text kept) |
| `POST /evidence/{id}/proposals` | Operator-selected region → tentative label/unread proposals (color measured, meaning unknown) |
| `POST /evidence/purge-trash` | `{"confirm": true}` required; deletes trashed evidence + unreferenced files; purge audit kept (no content) |
| `GET /evidence/export` | JSON download without image bytes or file paths; trash excluded unless `include_trash=true` |

Storage limits mirror the reference: 200 records (incl. trash) and 100 MB (`EVIDENCE_MAX_RECORDS`, `EVIDENCE_MAX_BYTES`).
When full, upload returns `507 STORAGE_FULL` and the page pauses its queue. Trash frees nothing until purged.

## Share source capture

`Share source` calls `navigator.mediaDevices.getDisplayMedia` only on an explicit click. The page shows a preview
and crop inputs; nothing is sampled until **Confirm crop & retain frames**. Frames are then sampled every 5 s
from the cropped region, but only when the queue is idle (backpressure), never while paused. A source size
change pauses sampling; ending the share, **Stop sharing**, the stop icon, or leaving the page stops it. Frames go
through the same `/api/ocr/extract` path as manual files with `source_mode=screen-capture`, capture time, source label,
original dimensions and crop. No clicks, scrolling or other interaction with WhatsApp is performed.
Queue: max 100 pending items; network/back-end/storage errors keep the item and pause the queue.

## Validation and resource limits (configurable via `.env`)

- Magic-byte signature must match the declared type and Pillow's decoded format.
- Dimensions are read from the header **before** decoding; `OCR_MAX_IMAGE_SIDE` (10,000 px) and `OCR_MAX_IMAGE_PIXELS` (25 MP) stop decompression bombs; Pillow's bomb warning is fatal; full decode catches truncation.
- `OCR_MAX_UPLOAD_BYTES` (10 MB) is enforced while streaming, in both Next.js and FastAPI.
- `OCR_TESSERACT_TIMEOUT_SECONDS` (20 s) kills the Tesseract subprocess; `OCR_MAX_CONCURRENCY` (1) with a bounded queue wait (`OCR_BUSY`).
- Screenshot content and OCR text are never logged.

## OCR engine (`backend/app/ocr/engine.py`)

Tesseract `eng` only, `--psm 11` (sparse text, as in the Python reference). Preprocessing: grayscale; 2× Lanczos upscale when narrower than 650 px. No EXIF rotation, so all word/line boxes are original-image pixels. Stored provenance: engine version, language, PSM, preprocessing steps, scale, parser version, duration.

## Chat-list parser (`backend/app/ocr/whatsapp_parser.py`)

Pure function over OCR lines; independently tested. Rows are anchored on right-aligned visible timestamps or phone-only headers.

- Only a header that is **entirely** `+91` + ten digits (6–9 start) becomes a phone `candidate`. Bare 10-digit, partial, foreign, or OCR-confusable (`O`, `l`, `I`) headers are `ambiguous` and quarantined; digits are never repaired. More than one `+number` in a header is a `conflict`.
- Phones inside message previews never become identities (warning only).
- Timestamps are kept as visible text; nothing is inferred when missing. Sender direction is always `unknown`.
- Repeated phones or repeated display names across rows are quarantined; names never merge identities.
- Landscape (desktop) screenshots: only the left 42 % is parsed, with a warning. The bottom 8 % navigation band of portrait screenshots is ignored.

## MySQL (Alembic revision `20261020_0001`)

| Table | Purpose |
| --- | --- |
| `screenshot_captures` | SHA-256, account label, size/dimensions, status, review-only status, cross-account flag, private evidence key, errors |
| `ocr_results` | one row per OCR attempt: engine provenance, raw text, confidence, word/line boxes (JSON), warnings, timing, errors |
| `ocr_row_observations` | parsed rows: header, phone candidate + status, preview, visible timestamp, bounds, confidence, warnings, quarantine |

MySQL `CHECK` constraints pin `review_status = 'unverified_review_only'`, `identity_status = 'unverified'`, `review_status = 'needs_review'` and `sender_direction = 'unknown'`.

**Duplicate scope:** exact whole-file SHA-256 within the same account label (unique key `(account_label, sha256)`; MySQL's default collation compares labels case-insensitively). A duplicate returns the stored result without re-running OCR. The same bytes under another account label are processed and stored separately and flagged `cross_account_reuse`. A previously failed capture is re-processed as a new attempt on re-upload. Visually similar but non-identical screenshots are **not** treated as duplicates.

## Evidence retention

When `SCREENSHOT_RETENTION_ENABLED=true` (default) the original bytes are stored content-addressed in `backend/.private_storage/screenshots/` (git-ignored, directory `0700`, files `0600`). No HTTP route serves them. **There is no automatic purge in Phase 2**; files and rows persist until deleted manually. Storage is not encrypted by the application — rely on disk encryption and OS account separation.

## Tests

```bash
backend/.venv/bin/pytest backend/tests     # MySQL tests run in a rolled-back transaction; skip if MySQL is unreachable
npm test && npm run lint && npm run build
```

`backend/tests/test_ocr_engine.py::test_real_english_tesseract_on_synthetic_chat_list` runs real Tesseract on a synthetic image (skipped when Tesseract is absent, e.g. in CI).

## Known limitations

- Not a trained WhatsApp model; no measured accuracy, precision/recall or row coverage. A rendered synthetic screenshot is not representative of real captures.
- The token is a single static local secret; any process running as the same OS user can read `.env`. There is no per-user login.
- Hindi OCR, CRM delivery and an automatic retention scheduler are not implemented.
- Real screen capture was browser-verified with Chromium's fake capture device (test pattern), not a live WhatsApp window.
