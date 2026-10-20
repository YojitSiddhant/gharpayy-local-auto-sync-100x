# Gharpayy Local Auto Sync 100X

Local-first operations system: Next.js frontend, FastAPI backend and MySQL persistence. Phase 2 adds review-only English OCR of WhatsApp screenshots at `/local-auto-sync` — see [docs/PHASE_2_OCR.md](docs/PHASE_2_OCR.md).

## Stack and structure

- Next.js 16 App Router, React, TypeScript, Tailwind CSS, ESLint, Zod
- FastAPI, Uvicorn, SQLAlchemy, Alembic, PyMySQL, Pydantic settings
- Vitest, React Testing Library, pytest, and GitHub Actions CI

The frontend lives in `app/` and `src/`; the backend lives in `backend/app/`, with tests in `backend/tests/` and migrations in `backend/alembic/`.

## Local setup

```bash
npm install
cp .env.example .env
/opt/homebrew/bin/python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
```

Set `DATABASE_URL`, `BACKEND_CORS_ORIGINS` and a server-only `LOCAL_API_TOKEN` (at least 32 random characters, never `NEXT_PUBLIC_`) in `.env`. Apply migrations with `backend/.venv/bin/alembic -c backend/alembic.ini upgrade head`. The example uses a local MySQL URL and contains no credentials. MySQL is optional for the health-check foundation; no tables are created automatically. The local setup uses the `gharpayy_local_auto_sync_100x` database over `127.0.0.1`.

For local database setup, start the Homebrew MySQL service, create the `gharpayy_local_auto_sync_100x` database with `utf8mb4` encoding and a least-privilege development user restricted to loopback, then place the resulting connection URL only in the ignored `.env` file. Verify with `SELECT 1`; do not run migrations until credentials are configured. SQLAlchemy authentication with MySQL 9.7 uses the pinned `cryptography` dependency.

Future OCR prerequisites are Tesseract, English language data, optional Hindi language data, pytesseract, Pillow, and OpenCV. Tesseract 5.5.3 with English data is available locally; Hindi data remains to be installed with `brew install tesseract-lang` when the package download is available.

Run both services with `npm run dev`, or separately with `npm run dev:frontend` and `npm run dev:backend`. The frontend uses `NEXT_PUBLIC_API_BASE_URL` for the local FastAPI URL. No backend credentials are exposed to the browser.

## Verification

```bash
npm run lint
npm test
npm run build
backend/.venv/bin/pytest backend/tests
```

`npm run audit` reports dependency vulnerabilities without applying fixes.

## Current limitations

Phase 2 implements manual screenshot upload, local English Tesseract OCR, conservative chat-list parsing and MySQL evidence storage. It does not implement Hindi OCR, screen capture, ML training, review/correction workflows, CRM delivery or lead creation. OCR accuracy has not been measured. See `docs/PHASE_2_OCR.md` for the security boundary, retention and limitations.

Reference projects are kept outside this repository and are read-only inputs: `lead-loved-flow` for visual direction and `Gharpayy_Local_Sync_FINAL` for future OCR dependency research. Production `Gharpayy-Ops` is not connected.
