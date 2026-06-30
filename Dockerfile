# ─────────────────────────────────────────────────────────────────────────────
#  Market — Production Dockerfile
#  Multi-stage: builder installs deps, final image is lean and non-root.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# System libs needed to compile psycopg2 + Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libjpeg-dev zlib1g-dev libwebp-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: final runtime image ──────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime libs only (no build tools in production)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libjpeg62-turbo zlib1g libwebp7 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy project source
COPY . .

# ── Security: run as non-root user ────────────────────────────────────────────
# Give the user a real home dir (/app) so gunicorn's control server and any
# tooling that writes to $HOME don't hit "Permission denied: /home/market".
RUN useradd --home-dir /app --shell /bin/false market \
 && mkdir -p /app/staticfiles /app/media \
 && chmod +x /app/docker-entrypoint.sh \
 && chown -R market:market /app
ENV HOME=/app

USER market

# Collect static at build time (Nginx serves them directly)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

# Entrypoint applies migrations, then starts gunicorn (honoring $PORT on Render).
CMD ["/app/docker-entrypoint.sh"]
