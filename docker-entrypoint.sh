#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
#  Container startup: apply DB migrations, then launch gunicorn.
#  Migrations run here (not at build time) because the database is only
#  reachable at runtime. This is idempotent — safe to run on every boot.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "==> Applying database migrations..."
python manage.py migrate --noinput

echo "==> Starting gunicorn on port ${PORT:-8000}..."
exec gunicorn mall_project.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --worker-class sync \
    --timeout 60 \
    --graceful-timeout 30 \
    --worker-tmp-dir /dev/shm \
    --access-logfile - \
    --error-logfile - \
    --log-level info
