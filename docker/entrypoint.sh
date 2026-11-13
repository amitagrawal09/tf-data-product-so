#!/bin/sh
# Maps SERVICE -> ASGI app and starts uvicorn behind a reverse proxy (APIM / Container Apps ingress).
set -eu
case "${SERVICE:-}" in
  experience|process|system|gateway) ;;
  *) echo "SERVICE must be one of experience|process|system|gateway (got '${SERVICE:-}')" >&2; exit 64 ;;
esac
exec uvicorn "tfso.${SERVICE}.app:app" \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --timeout-keep-alive 75 \
  --log-level "${LOG_LEVEL:-info}"
