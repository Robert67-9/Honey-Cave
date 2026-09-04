#!/bin/bash
# ═════════════════════════════════════════════════════════════════════════════
#  Market — Production Deployment Script
#  Run this on your server after uploading the project.
#
#  Usage:
#    First deploy:   bash deploy.sh
#    Update code:    bash deploy.sh --update
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

COMPOSE="docker compose"
UPDATE=false
[[ "${1:-}" == "--update" ]] && UPDATE=true

echo ""
echo "═══════════════════════════════════════════════════"
echo "   Market — Production Deploy"
echo "═══════════════════════════════════════════════════"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "❌  .env file not found."
    echo "    Copy the template:  cp .env.example .env"
    echo "    Then fill in all CHANGE-ME values."
    exit 1
fi

# Check SECRET_KEY isn't the placeholder
if grep -q "CHANGE-ME" .env; then
    echo "❌  .env still contains placeholder values (CHANGE-ME)."
    echo "    Please update all secrets before deploying."
    exit 1
fi

# Check SSL certs exist
if [ ! -f "nginx/ssl/fullchain.pem" ] || [ ! -f "nginx/ssl/privkey.pem" ]; then
    echo "⚠️   SSL certificates not found in nginx/ssl/"
    echo "    See nginx/ssl/README.txt for setup instructions."
    echo "    Continuing without SSL (HTTP only)..."
fi

# ── Build & start ─────────────────────────────────────────────────────────────
if $UPDATE; then
    echo ""
    echo "🔄  Updating — pulling latest code & rebuilding..."
    $COMPOSE build --no-cache web
    $COMPOSE up -d --no-deps web nginx
    $COMPOSE exec web python manage.py migrate --noinput
    $COMPOSE exec web python manage.py collectstatic --noinput
    echo "✅  Update complete."
else
    echo ""
    echo "🚀  First deployment — starting all services..."
    $COMPOSE pull db redis nginx
    $COMPOSE build web
    $COMPOSE up -d db redis
    echo "⏳  Waiting for database to be ready..."
    sleep 8
    $COMPOSE up -d web
    echo "⏳  Waiting for Django to start..."
    sleep 5
    $COMPOSE exec web python manage.py migrate --noinput
    $COMPOSE exec web python manage.py collectstatic --noinput

    echo ""
    read -p "   Seed sample products and branches? (y/n): " SEED
    if [[ "$SEED" =~ ^[Yy]$ ]]; then
        $COMPOSE exec web python manage.py seed_branches
        $COMPOSE exec web python manage.py populate_data
        echo "✅  Sample data loaded."
    fi

    echo ""
    read -p "   Create admin superuser? (y/n): " SU
    if [[ "$SU" =~ ^[Yy]$ ]]; then
        $COMPOSE exec web python manage.py createsuperuser
    fi

    $COMPOSE up -d nginx
fi

# ── Status ────────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════"
echo "   ✅  Deployment complete!"
echo ""
$COMPOSE ps
echo ""
echo "   🌐  Site:        https://$(grep ALLOWED_HOSTS .env | cut -d= -f2 | cut -d, -f1)"
echo "   🔧  Admin panel: https://$(grep ALLOWED_HOSTS .env | cut -d= -f2 | cut -d, -f1)/panel/"
echo ""
echo "   Useful commands:"
echo "   View logs:     docker compose logs -f web"
echo "   DB shell:      docker compose exec db psql -U market_user -d market"
echo "   Django shell:  docker compose exec web python manage.py shell"
echo "   Restart:       docker compose restart web"
echo "   Stop site:     docker compose stop        (graceful — drains requests)"
echo "   Stop & remove: docker compose down        (stops all, removes containers)"
echo "═══════════════════════════════════════════════════"
