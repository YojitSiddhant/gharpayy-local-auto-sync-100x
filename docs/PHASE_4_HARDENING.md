# Phase 4 — Hardening, security, performance and reliability

UI unchanged. One additive migration (`20261020_0003`). No new services.

## Architecture (current)

```
Browser  /  and  /local-auto-sync   (one Next.js page, reference-matching UI)
  │ same-origin fetch + x-gharpayy-client header
  ▼
Next.js route handlers  /api/ocr/extract , /api/local/evidence/...   (loopback Host, Origin, Sec-Fetch-Site,
  │                                                                    allowlisted paths/params, body caps)
  │ + server-only LOCAL_API_TOKEN
  ▼
FastAPI 127.0.0.1:8000  /api/v1/ocr/extract , /api/v1/evidence/...   (loopback peer, loopback Host, no foreign
  │                                                                    Origin, token, 1 OCR slot, body caps)
  ├─ English Tesseract 5.5.3 (local subprocess, 20 s timeout)
  ├─ MySQL `gharpayy_local_auto_sync_100x` (SQLAlchemy + Alembic, head 20261020_0003)
  └─ backend/.private_storage/screenshots/  (0700 dir, 0600 files, content-addressed, git-ignored)
```

## Local development

```bash
npm install
/opt/homebrew/bin/python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cp .env.example .env            # set DATABASE_URL and a 32+ char LOCAL_API_TOKEN (never NEXT_PUBLIC_)
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
npm run dev                     # Next.js on 127.0.0.1:3000, FastAPI on 127.0.0.1:8000
```

Requirements: MySQL 8.0.16+ (CHECK constraints; developed on 9.7.1) with a dedicated `utf8mb4` database and a
loopback-only user; Tesseract 5.x with English data (`tesseract --list-langs` must list `eng`).
`npm run dev` now starts FastAPI with the project venv (`backend/.venv/bin/python`) so the audited dependency
versions are the ones actually serving requests. **Restart `npm run dev` after upgrading Python packages.**

## A. Concurrent-edit protection

- `screenshot_captures.revision` (migration `20261020_0003`, additive, default 0). Every evidence mutation
  (correction, label, observed name, review, trash, restore, retry, region proposal) increments it.
- Mutations first run `UPDATE … SET revision = revision + 1 WHERE id = ? AND revision = ?`. InnoDB serialises
  concurrent writers on the row lock, so two edits based on the same revision cannot both succeed: the second
  receives `409 EDIT_CONFLICT` (`details.current_revision`, `details.expected_revision`). Nothing partial is
  committed; the correction row, corrected text, review state and audit event commit together.
- `PATCH /evidence/{id}` and `POST /evidence/{id}/review` **require** `expected_revision`; trash/restore/retry
  accept it optionally.
- Page behaviour: each draft remembers the revision it started from. On conflict the typed text is kept,
  autosave / Save-retry / unload-save are blocked for that reading, and an **Edit conflict** box shows the latest
  saved text with two explicit choices: *Load latest (discard my changes)* or *Save my version over latest*.
  Mark reviewed on a stale view is refused and the latest text is loaded.

## B. Evidence storage and retention

- The evidence file key is committed **before** OCR runs, so an interruption cannot orphan a file and Retry OCR can
  still find it. A failed disk write is reported as a result warning (previously silent).
- `store_evidence` fsyncs before the atomic rename, and reuses an existing file only if it is a regular,
  non-symlink file of the right size; otherwise it is atomically replaced. Symlinked evidence is never served.
- Storage consistency check (dry run by default, counts only):

  ```bash
  backend/.venv/bin/python -m backend.app.ocr.storage_check                  # report
  backend/.venv/bin/python -m backend.app.ocr.storage_check --remove-orphans # delete ONLY unreferenced files
  ```

  Reports referenced/missing/orphan/stale-temp/unexpected entries. `--remove-orphans` deletes only plain files in
  the evidence directory that no record references (re-checked per file) and `.incoming-*` temp files older than
  an hour. It never edits or deletes database rows and never touches unexpected files or symlinks.
- Retention is unchanged: disabled by default, dry run unless `--apply`, moves to Trash only (reversible,
  audited); permanent deletion only via *Purge trash… → Confirm permanent purge*.

## Crash recovery (non-destructive)

| Situation | What happens / what to do |
| --- | --- |
| Backend stopped mid-OCR | On the next page load the reading becomes **Failed (OCR_INTERRUPTED)**; use **Retry OCR**. |
| Network/backend error during a queue | The screenshot stays queued and the queue pauses; start the backend, press **Resume**. |
| Storage full (200 records / 100 MB incl. trash) | Queue pauses. Trash then **Purge trash…**, or run retention, then **Resume**. |
| Suspected file/database mismatch | Run the storage check (dry run) and read the counts before choosing `--remove-orphans`. |
| Migration state unclear | `backend/.venv/bin/alembic -c backend/alembic.ini current` — never drop or reset the database. |

## C. Local API security review

Confirmed and fixed:

- **Private data in logs.** Uvicorn's access log and Next's dev request log printed full query strings, so evidence
  searches (phone numbers, message words) reached the terminal. FastAPI now runs with `--no-access-log`; Next skips
  logging `/api/local/*` and `/api/ocr/*` (`logging.incomingRequests.ignore`).
- **Forwarded headers.** Uvicorn trusted `X-Forwarded-For` from 127.0.0.1 by default; it now runs with
  `--no-proxy-headers`, so the loopback check uses only the socket peer.
- **Interpreter drift.** `dev:backend` used whichever `python3` was on PATH; it now uses the project venv.
- Symlink handling in evidence storage (see B).

Verified unchanged and adequate: token never in browser bundles or responses; FastAPI CORS GET-only and not used
as auth; CSRF blocked by the custom header (preflight) plus Origin/Sec-Fetch-Site checks; DNS rebinding blocked by
Host checks; upload signature/decoder/decompression limits; 10 MB upload and 1 MB JSON caps at both layers;
error bodies carry codes and field names only; export has no image bytes or paths; evidence paths are validated
against a strict key pattern inside the evidence directory; OCR concurrency bounded to one slot.

**Known limitation (not changed — needs real authentication):** the Next.js proxy injects the token for any
caller that can reach `127.0.0.1:3000` and send the custom header. Browsers on other sites cannot (CORS preflight),
but **another OS user or process on the same computer can read evidence through the proxy**. Use a single-user
machine account, or add real user authentication before multi-user use. FastAPI `/docs` and `/openapi.json`
(schema only, no data) are reachable on loopback.

## D. Dependency audit (performed 2026-10-20)

**Python** (OSV database; package names and versions only were sent):

| Package | Before | After | Why |
| --- | --- | --- | --- |
| Pillow | 11.0.0 (34 advisories, HIGH) | 12.3.0 | Decodes untrusted screenshots — directly reachable |
| starlette | 0.41.3 (14 advisories, HIGH) | 1.3.1 (pinned explicitly) | Web framework on the request path |
| fastapi | 0.115.6 | 0.133.0 | Oldest release allowing a fixed Starlette |
| cryptography | 44.0.0 (12 advisories, HIGH) | 50.0.2 | Used by PyMySQL authentication |
| pytest | 8.3.4 (2 advisories) | 9.0.3 | Dev/test only |

After the upgrade, 44 installed packages: **0 known advisories**; all backend tests pass; MySQL auth verified.

**npm:** production dependencies (`next`, `react`, `react-dom`, `zod`): **0 vulnerabilities**. `vite` was
updated 5.4.11 → 5.4.21 (within the existing range; lockfile changed only for vite), fixing the 5.4.x
`server.fs.deny` bypasses. Remaining findings are **dev/test tooling only** and are not reachable at runtime:

| Package (severity) | Fix offered | Decision |
| --- | --- | --- |
| vitest / tinypool / @vitest/mocker / vite-node (critical/moderate) | vitest 5 (major) | Deferred: major test-runner upgrade. Vitest UI is not used; tinypool options are not attacker-controlled. |
| vite, esbuild (high/moderate) | vite ≥ 6.4.3 (major) | Deferred: no Vite dev server is ever started (Vitest runs in-process; Next uses Turbopack). Windows-only items N/A. |
| eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces (high) | "downgrade to eslint-config-next 14" | Rejected: would break Next 16 linting. ReDoS needs attacker-controlled glob patterns; patterns come from repo config. |

`npm audit fix --force` was **not** run.

## E. Queue and capture performance

- Exact-pixel frame comparison now compares 32-bit words instead of `Array.prototype.every` — identical
  semantics (any differing byte ⇒ changed). Measured in Node 26 / V8, identical frames, 20 runs each:

  | Frame | `every` | 32-bit words |
  | --- | --- | --- |
  | 720×1280 | 15.71 ms | 0.39 ms |
  | 1440×900 | 22.78 ms | 0.56 ms |
  | 1920×1080 | 35.26 ms | 1.20 ms |

- In-browser queue memory budget: 200 MB of queued screenshots (previously only a 100-item cap, i.e. up to ~1 GB).
  Screenshots beyond it are rejected with an explicit message, never dropped silently.
- Unchanged by design: 5 s interval, single OCR slot, sampling only when the queue is idle (at most one frame
  waiting), FIFO order with failed items re-queued at the front, error-based pausing.

## F. Benchmark (bounded, synthetic, repeatable)

```bash
backend/.venv/bin/python -m backend.benchmarks.ocr_benchmark                  # 20 images
backend/.venv/bin/python -m backend.benchmarks.ocr_benchmark --count 40 --db  # + MySQL timings, rolled back
```

Synthetic 720×1280 chat-list images generated in memory (max 200), every 5th an exact duplicate. `--db` performs
inserts/list/detail inside one transaction that is always rolled back (nothing persisted; verified 0 rows after).

**Measured 2026-10-20** on this Mac (Apple Silicon, macOS 27, Python 3.11.17, Tesseract 5.5.3), `--count 40 --db`:

| Stage | avg | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| Validation (signature + decode limits) | 2.29 ms | 2.33 | 2.39 | 2.40 |
| SHA-256 | 0.03 ms | 0.03 | 0.03 | 0.03 |
| Tesseract OCR | 148.22 ms | 147.78 | 150.17 | 170.29 |
| Parsing | 1.29 ms | 1.28 | 1.33 | 1.45 |
| End-to-end, new image | 151.82 ms | 151.47 | 153.85 | 174.09 |
| End-to-end, exact duplicate (no OCR) | 2.38 ms | 2.39 | 2.42 | 2.42 |
| MySQL insert capture + result + 6 rows | 3.18 ms | 2.97 | 3.35 | 9.34 |
| MySQL list query | 1.19 ms | 1.16 | 1.64 | 1.79 |
| Detail serialisation | 1.95 ms | 1.66 | 1.78 | 13.13 |

40 images, 33 unique, 7 duplicates, 0 failures (0 %), 198 rows parsed; process max RSS 83.8 MB.
Derived (not measured end-to-end) sequential OCR capacity from the new-image mean: ~23,700 synthetic
screenshots/hour. This excludes browser capture, upload, network and MySQL time and uses clean synthetic images;
**it is not evidence for any real WhatsApp throughput target (e.g. 1,800/day)**. Real screenshots are larger and
noisier; measure them during the planned manual session.

## Tests and CI

- Backend: `backend/.venv/bin/pytest backend/tests -rs`. MySQL-backed tests run against the dev database inside
  rolled-back transactions (the two-connection concurrency test commits one uniquely-labelled row and deletes it).
  Without MySQL they are **skipped with an explicit reason**; `REQUIRE_MYSQL_TESTS=1` turns those skips into failures.
- CI (`.github/workflows/ci.yml`) has no MySQL service, so MySQL-backed tests are listed as skipped (`-rs`).

## Release limitations

Not manually verified: real WhatsApp capture, OCR accuracy, background-tab timing, minimized-window behaviour,
the conflict box in two real browser tabs. OCR accuracy is unmeasured. English only. Single static local token;
no user accounts. No automatic retention scheduler.
