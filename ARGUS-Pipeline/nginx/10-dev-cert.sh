#!/bin/sh
# Runs before nginx starts. Development: create a self-signed certificate if none is mounted.
# Production (ARGUS_ENV=production): never generate one; refuse to start without the real certificate.
set -eu
CERT=/etc/nginx/certs/tls.crt
KEY=/etc/nginx/certs/tls.key
if [ -s "$CERT" ] && [ -s "$KEY" ]; then
  echo "[argus-nginx] using certificate from /etc/nginx/certs"
  exit 0
fi
if [ "${ARGUS_ENV:-development}" = "production" ]; then
  echo "[argus-nginx] ARGUS_ENV=production but $CERT / $KEY are missing. Mount the real certificate via TLS_CERT_DIR." >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then   # slim/alpine variants ship without the CLI
  if command -v apk >/dev/null 2>&1; then apk add -q openssl; else apt-get update -qq && apt-get install -y -qq openssl; fi
fi
echo "[argus-nginx] generating a self-signed development certificate (browsers will warn)"
mkdir -p /etc/nginx/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 365 -keyout "$KEY" -out "$CERT" \
  -subj "/CN=localhost/O=ARGUS development" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 "$KEY"
