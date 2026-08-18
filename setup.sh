#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Market — Local Development Setup
#  For production deployment, use:  bash deploy.sh
# ═════════════════════════════════════════════════════════════════════════════
set -e

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Market — Local Dev Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f .env ]; then
    cp .env.example .env
    # Override to dev-friendly defaults
    sed -i 's/DEBUG=False/DEBUG=True/' .env
    sed -i 's/SECURE_SSL_REDIRECT=True/SECURE_SSL_REDIRECT=False/' .env
    sed -i 's/SESSION_COOKIE_SECURE=True/SESSION_COOKIE_SECURE=False/' .env
    sed -i 's/CSRF_COOKIE_SECURE=True/CSRF_COOKIE_SECURE=False/' .env
    sed -i 's/DB_HOST=db/DB_HOST=localhost/' .env
    echo "✅  Created .env (dev mode)"
fi

echo "📦 Installing dependencies..."
pip install -r requirements.txt

echo "🗄️  Running migrations..."
python manage.py migrate

echo "🎨 Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo "🌱 Populating sample data..."
python manage.py seed_branches   2>/dev/null && echo "  ✅ Branches" || echo "  ↩  Skipping branches"
python manage.py populate_data   2>/dev/null && echo "  ✅ Products" || echo "  ↩  Skipping products"

read -p "Create superuser? (y/n): " CS
if [ "$CS" = "y" ] || [ "$CS" = "Y" ]; then
    python manage.py createsuperuser
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Dev setup complete!"
echo "  Start:        python manage.py runserver"
echo "  Store:        http://127.0.0.1:8000/"
echo "  Admin panel:  http://127.0.0.1:8000/panel/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
