# FastAPI Render Docker deployment

Render service settings:

- Environment: Docker
- Dockerfile path: `backend/Dockerfile`
- Docker context: repository root
- Start command: leave empty; the image entrypoint starts Uvicorn

The image includes the base Python dependencies, the existing PaddleOCR runtime versions, native English Tesseract, OpenCV system libraries, and the existing subprocess worker. PaddleOCR model assets are not committed; PaddleOCR may download them into `PADDLE_HOME` on first use, so model initialization and memory use must be verified on the selected Render instance.

Required runtime configuration:

- `PORT` is supplied by Render automatically.
- `APP_ENV=render`
- `BACKEND_ALLOWED_HOSTS=["your-backend.onrender.com"]`
- `BACKEND_CORS_ORIGINS=["https://your-frontend.onrender.com"]`
- `LOCAL_API_TOKEN` equal to the frontend proxy token, at least 32 random characters
- `AIVEN_CA_CERT` containing the Aiven project CA PEM, supplied as a secret environment value
- `DATABASE_URL` using the Aiven host/port and query parameters `ssl_ca=%2Frun%2Fsecrets%2Faiven-ca.pem&ssl_check_hostname=true`
- `OCR_ENGINE=paddle_fallback` to retain PaddleOCR with Tesseract fallback

Do not run migrations automatically from the image. Apply them only after the database and deployment have been independently reviewed. Local development continues to use the existing virtual environments and `.env` files; this Docker image is deployment-only.
