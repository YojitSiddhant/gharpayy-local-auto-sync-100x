#!/bin/sh
set -eu

# Aiven's CA certificate is supplied at runtime, never committed or baked into the image.
# DATABASE_URL must include ssl_ca=%2Frun%2Fsecrets%2Faiven-ca.pem and ssl_check_hostname=true.
if [ -n "${AIVEN_CA_CERT:-}" ]; then
  umask 077
  printf '%s\n' "$AIVEN_CA_CERT" > "${AIVEN_CA_CERT_PATH:-/run/secrets/aiven-ca.pem}"
fi

if [ ! -s "${AIVEN_CA_CERT_PATH:-/run/secrets/aiven-ca.pem}" ]; then
  echo "AIVEN_CA_CERT is required for verified Aiven MySQL TLS" >&2
  exit 1
fi

exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
