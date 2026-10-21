# Render Free preparation

This repository is prepared for a two-service Render deployment, but is not a deployment declaration and must not be deployed without completing the blockers below.

## Frontend service

- Build command: `npm ci && npm run build`
- Start command: `npm start`
- `npm start` binds Next.js to `0.0.0.0` and uses Render's `PORT` (defaulting to `3000` only for local invocation).
- Set `NEXT_ALLOWED_HOSTS` to the exact Render frontend hostname. Do not use `*`.
- Set `BACKEND_INTERNAL_URL` to the backend service URL and set a random `LOCAL_API_TOKEN` of at least 32 characters.

## Backend service

- Intended command: `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
- Set `APP_ENV=production` or `APP_ENV=render`.
- Set `BACKEND_ALLOWED_HOSTS` to the exact backend hostname and `BACKEND_CORS_ORIGINS` to the exact frontend HTTPS origin.
- Set `LOCAL_API_TOKEN` to the same server-only value used by the frontend.
- Set `DATABASE_URL` to an external TLS-enabled MySQL service.

The backend permits non-loopback clients only in the explicit production modes above, and still requires the configured Host, configured Origin where supplied, and server-only token. Local development remains loopback-only by default.

## Not included yet

PaddleOCR dependencies/model assets, native Tesseract packaging, and persistent private screenshot storage are not declared here because they require Linux/runtime verification and provider credentials. Render Free's filesystem is ephemeral; do not enable production screenshot retention until an authenticated private object-storage adapter is implemented and tested.

Do not put database, storage, OCR-provider, or token credentials in this repository or in `NEXT_PUBLIC_*` variables.
