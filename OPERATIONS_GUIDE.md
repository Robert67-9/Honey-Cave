# Honey Cave Market — Operations Guide

---

## 1. Running the project locally (Windows / Mac / Linux)

### First-time setup

```powershell
# 1. Unzip the project and open a terminal in the folder
cd HoneyCaveMarket

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac / Linux

# 3. Install all dependencies
pip install -r requirements.txt

# 4. Run database migrations
python manage.py migrate

# 5. Create your admin account
python manage.py createsuperuser

# 6. (Optional) Load sample products and branches
python manage.py populate_data
python manage.py seed_branches

# 7. Start the development server
python manage.py runserver
```

Then open:
- **Store:** http://127.0.0.1:8000/
- **Admin panel:** http://127.0.0.1:8000/panel/

### Every time after that

```powershell
venv\Scripts\activate
python manage.py runserver
```

---

## 2. Production-ready Django app

The app is hardened for live use. Key settings in `.env`:

```
# Flip these before going live
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
SECRET_KEY=<generate a new one — see below>
SITE_URL=https://yourdomain.com

# Paystack live keys (from dashboard.paystack.com)
PAYSTACK_PUBLIC_KEY=pk_live_...
PAYSTACK_SECRET_KEY=sk_live_...

# HTTPS security (uncomment in .env)
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=31536000
```

Generate a new SECRET_KEY:
```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 3. SEO-optimized templates

Every page already has meta tags, Open Graph tags, and structured data. To customize them, edit `mall/templates/mall/base.html`:

```html
<!-- Around line 20 — update these for your brand -->
<meta name="description" content="Your store description">
<meta property="og:site_name" content="Honey Cave Market">

<!-- Around line 55 — update social links in the JSON-LD block -->
"sameAs": [
  "https://facebook.com/honeycavemarket",
  "https://instagram.com/honeycavemarket"
]
```

Per-product SEO is automatic — each product uses its `name`, `description`, and `image` fields for meta tags in `product_detail.html`.

---

## 4. Sitemaps & robots.txt

### Sitemap
Your sitemap is live at:
```
https://yourdomain.com/sitemap.xml
```
It auto-includes all available products and categories. No setup needed — it updates automatically as you add products.

To submit it to Google:
1. Go to [Google Search Console](https://search.google.com/search-console)
2. Add your domain
3. Click **Sitemaps** → paste `https://yourdomain.com/sitemap.xml` → Submit

### robots.txt
Served at `https://yourdomain.com/robots.txt`. The file is at:
```
mall/templates/mall/robots.txt
```
It already blocks admin, checkout, and account pages from indexing, and allows product images. Edit it if needed.

---

## 5. WhiteNoise for static files

WhiteNoise serves your CSS, JS, and images directly from Django — no separate Nginx static file setup needed.

### Setup (already done — just run these two commands)

```powershell
# 1. Collect all static files into one folder
python manage.py collectstatic --noinput

# 2. That's it — WhiteNoise serves them automatically
python manage.py runserver
```

WhiteNoise also compresses files (gzip + brotli) and adds cache-busting hashes to filenames automatically in production.

### If static files look broken
```powershell
python manage.py collectstatic --noinput --clear
```

---

## 6. Security hardening

The following is already in place — nothing to configure:

| Protection | How it works |
|---|---|
| CSRF | All forms protected by Django's CSRF tokens |
| Rate limiting | Login, OTP, checkout, and promo endpoints limited per IP |
| OTP lockout | 10 failed OTP attempts = 1-hour account lockout |
| Security headers | `X-Frame-Options`, `X-Content-Type-Options`, `CSP`, `Referrer-Policy` set on every response |
| Suspicious request blocking | PHP scanners, SQL injection attempts, path traversal blocked before routing |
| Password reset session | Full session flush after password reset |
| Rider portal CSRF | Token re-validation on every POST |
| Atomic stock | F() updates prevent overselling under concurrent traffic |
| Admin 2FA | TOTP available at `/panel/2fa/setup/` |
| Audit log | Every admin action logged at `/panel/audit-log/` |

---

## 7. Deployment files explained

| File | What it does |
|---|---|
| `deploy.sh` | One-command Docker deploy for a Linux VPS — runs migrations, collects static files, seeds data, creates superuser |
| `setup.sh` | One-command local dev setup |
| `Dockerfile` | Builds the Django app container |
| `docker-compose.yml` | Wires together Django + PostgreSQL + Redis + Nginx |
| `nginx/nginx.conf` | Nginx config — HTTPS, proxy to gunicorn |
| `nginx/ssl/README.txt` | Instructions for getting free SSL via Let's Encrypt |
| `.env` | All secrets and config — **never commit this to Git** |

### Deploy to a Linux VPS (e.g. DigitalOcean, AWS, Hetzner)

```bash
# On your server — one command does everything
bash deploy.sh

# Update after code changes
bash deploy.sh --update
```

### Useful Docker commands once live

```bash
docker compose logs -f web          # Watch live logs
docker compose exec web python manage.py shell   # Django shell
docker compose exec web python manage.py migrate  # Run new migrations
docker compose restart web          # Restart the app
docker compose stop                 # Graceful shutdown
docker compose down                 # Stop and remove containers
```

---

## 8. Admin panel quick reference

| URL | What you can do |
|---|---|
| `/panel/` | Dashboard — revenue, orders, low stock |
| `/panel/products/` | Add / edit / delete products |
| `/panel/orders/` | View and manage all orders |
| `/panel/inventory/` | Bulk restock low-stock items |
| `/panel/csv-import/` | Bulk import products, branches, promo codes via CSV |
| `/panel/audit-log/` | See every admin action with actor, IP, timestamp |
| `/panel/2fa/setup/` | Enable 2-factor authentication on your account |
| `/panel/ai-insights/` | AI-generated sales analysis |
