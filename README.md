# Gharpayy Local Auto Sync 100X

Phase 1 foundation for a local-first operations system: Next.js frontend, FastAPI backend, and MySQL-ready persistence configuration.

## Stack and structure

- Next.js 16 App Router, React, TypeScript, Tailwind CSS, ESLint, Zod
- FastAPI, Uvicorn, SQLAlchemy, Alembic, PyMySQL, Pydantic settings
- Vitest, React Testing Library, pytest, and GitHub Actions CI

The frontend lives in `app/` and `src/`; the backend lives in `backend/app/`, with tests in `backend/tests/` and migrations in `backend/alembic/`.

## Local setup

```bash
npm install
cp .env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
```

Set `DATABASE_URL` and `BACKEND_CORS_ORIGINS` in `.env` or `backend/.env`. The example uses a local MySQL URL and contains no credentials. MySQL is optional for the health-check foundation; no tables are created automatically.

Run both services with `npm run dev`, or separately with `npm run dev:frontend` and `npm run dev:backend`. The frontend uses `NEXT_PUBLIC_API_BASE_URL` for the local FastAPI URL. No backend credentials are exposed to the browser.

## Verification

```bash
npm run lint
npm test
npm run build
backend/.venv/bin/pytest backend/tests
```

`npm run audit` reports dependency vulnerabilities without applying fixes.

## Current limitations and future architecture

Phase 1 does not implement OCR, ML, screenshot capture, review workflows, CRM integration, dashboards, or business-domain tables. Future phases can add isolated OCR and capture services under `backend/app/ocr/` and `backend/app/capture/`, expose reviewed results through versioned APIs, and add domain migrations after the persistence contract is agreed.

Reference projects are kept outside this repository and are read-only inputs: `lead-loved-flow` for visual direction and `Gharpayy_Local_Sync_FINAL` for future OCR dependency research. Production `Gharpayy-Ops` is not connected.
