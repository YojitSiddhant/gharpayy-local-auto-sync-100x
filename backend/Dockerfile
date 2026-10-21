FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIVEN_CA_CERT_PATH=/run/secrets/aiven-ca.pem \
    PADDLE_HOME=/var/cache/paddle

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       libglib2.0-0 \
       libgl1 \
       tesseract-ocr \
       tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements-render-paddle.txt backend/requirements-render-paddle.txt
COPY backend/requirements.txt backend/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r backend/requirements-render-paddle.txt

COPY backend backend

# Reference repositories, virtual environments, private evidence, and model caches are
# excluded by the root .dockerignore and are never copied into the image.
RUN mkdir -p /run/secrets /var/cache/paddle \
    && chmod 700 /run/secrets /var/cache/paddle

ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]
