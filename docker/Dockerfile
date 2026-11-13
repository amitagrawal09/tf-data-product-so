# syntax=docker/dockerfile:1.7
# One image for every Tier-2 service. SERVICE selects the ASGI app:
#   experience | process | system | gateway
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SERVICE=process \
    PORT=8000 \
    TFSO_FRONTEND_DIR=/app/frontend

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 tfso \
 && useradd --system --uid 10001 --gid tfso --home-dir /app --shell /usr/sbin/nologin tfso

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
COPY cdm ./cdm
RUN pip install --no-deps .

COPY frontend ./frontend
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
 && mkdir -p /app/data/lakehouse /app/data/run \
 && chown -R tfso:tfso /app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
