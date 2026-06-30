from pathlib import Path
import os
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

import sys
import logging
logger = logging.getLogger(__name__)

# No default — forces an explicit SECRET_KEY in .env. A missing key raises
# ImproperlyConfigured at startup, which is far safer than silently using
# a known insecure placeholder.
SECRET_KEY = config('SECRET_KEY')

# FIX-1: Default changed to False — must explicitly set DEBUG=True in .env for local dev.
# Previously defaulted to True, which would expose tracebacks if DEBUG was accidentally
# omitted from Render's environment variables.
DEBUG = config('DEBUG', default=False, cast=bool)

# ALLOWED_HOSTS can be provided via env but may include scheme/ports in some
# environments/tools. Normalize entries to hostnames/IPs so Django's check
# doesn't reject valid local hosts like "127.0.0.1:8000" or "http://localhost".
import re
_raw_allowed = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())
ALLOWED_HOSTS = []
for _h in _raw_allowed:
    h = (_h or '').strip()
    # strip http(s):// prefix if present
    h = re.sub(r'^https?://', '', h)
    # remove trailing slash
    h = h.rstrip('/')
    # drop port if present
    if ':' in h:
        h = h.split(':', 1)[0]
    if h:
        ALLOWED_HOSTS.append(h)
# Always allow Render's internal health-check and preview domains
RENDER_HOSTNAME = config('RENDER_EXTERNAL_HOSTNAME', default='')
if RENDER_HOSTNAME and RENDER_HOSTNAME not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(RENDER_HOSTNAME)
# Allow all *.onrender.com subdomains automatically
if not any('.onrender.com' in h for h in ALLOWED_HOSTS):
    ALLOWED_HOSTS.append('.onrender.com')

# ─── Production safety checks ─────────────────────────────────────────────────
# Raise a clear error at startup rather than silently running with insecure defaults.
# Skipped during management commands (migrate, collectstatic, etc.) for convenience.
_mgmt_cmds = {'migrate', 'collectstatic', 'createsuperuser', 'create_admin', 'shell', 'test', 'seed_branches', 'populate_data'}
_is_mgmt = len(sys.argv) > 1 and sys.argv[1] in _mgmt_cmds
if not DEBUG and not _is_mgmt:
    if 'insecure' in SECRET_KEY:
        raise RuntimeError(
            'FATAL: SECRET_KEY still contains "insecure". '
            'Generate a real secret key and set it in .env before deploying.'
        )
    if set(ALLOWED_HOSTS) == {'*'}:
        raise RuntimeError(
            'FATAL: ALLOWED_HOSTS=* in production. '
            'Set your real domain(s) in .env (e.g. ALLOWED_HOSTS=honeycavemarket.com,www.honeycavemarket.com).'
        )

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'cloudinary',           # FIX-2: Cloudinary for persistent media storage
    'cloudinary_storage',   # FIX-2: replaces ephemeral FileSystemStorage for uploads
    'mall',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',         # static files — must be second
    'mall.middleware.BlockSuspiciousRequestsMiddleware',   # block scanners/exploits
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'mall.middleware.MaintenanceModeMiddleware',           # maintenance mode — after auth so request.user is set
    'mall.middleware.SecurityHeadersMiddleware',           # security response headers
]

ROOT_URLCONF = 'mall_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'mall.context_processors.cart_count',
                'mall.context_processors.branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'mall_project.wsgi.application'

# ─── Database ─────────────────────────────────────────────────────────────────
# Two ways to configure the database, in priority order:
#   1. DATABASE_URL  — single connection string (Railway, Render, Heroku style)
#                      e.g. postgresql://user:pass@host:5432/dbname
#   2. DB_ENGINE + DB_NAME + DB_USER + ... — split fields (manual / docker-compose)
# Falls back to SQLite for quick local dev without setting anything.

DATABASE_URL = config('DATABASE_URL', default='')

if DATABASE_URL:
    import urllib.parse as _urlparse
    _urlparse.uses_netloc.append('postgres')
    _urlparse.uses_netloc.append('postgresql')
    _u = _urlparse.urlparse(DATABASE_URL)
    DATABASES = {
        'default': {
            'ENGINE':   'django.db.backends.postgresql',
            'NAME':     (_u.path or '/').lstrip('/'),
            'USER':     _u.username or '',
            'PASSWORD': _u.password or '',
            'HOST':     _u.hostname or '',
            'PORT':     str(_u.port or 5432),
            'OPTIONS':  {'connect_timeout': 10},
            'CONN_MAX_AGE': 600,
        }
    }
else:
    DB_ENGINE = config('DB_ENGINE', default='django.db.backends.sqlite3')

    if DB_ENGINE == 'django.db.backends.sqlite3':
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': DB_ENGINE,
                'NAME':     config('DB_NAME',     default='market'),
                'USER':     config('DB_USER',     default='market_user'),
                'PASSWORD': config('DB_PASSWORD', default='market_pass'),
                'HOST':     config('DB_HOST',     default='localhost'),
                'PORT':     config('DB_PORT',     default='5432'),
                'OPTIONS': {
                    'connect_timeout': 10,
                },
            }
        }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Accra'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
# MEDIA_ROOT defaults to the in-repo media/ folder (committed images live here
# and are served in production by mall_project/urls.py). To make NEW uploads
# survive deploys on Render without a remote store, attach a Render Persistent
# Disk and set MEDIA_ROOT to its mount path (e.g. /var/data/media).
MEDIA_ROOT = config('MEDIA_ROOT', default=str(BASE_DIR / 'media'))

# ─── Storage ──────────────────────────────────────────────────────────────────
# FIX-2: Render's filesystem is ephemeral — uploaded files are wiped on every
# deploy or restart. Cloudinary is used for media (user uploads) in production.
# Whitenoise continues to serve static files (CSS/JS/images bundled at build time).
#
# Required env vars (get from https://cloudinary.com/console):
#   CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
#
# Install: pip install cloudinary django-cloudinary-storage

_cloudinary_configured = all([
    config('CLOUDINARY_CLOUD_NAME', default=''),
    config('CLOUDINARY_API_KEY',    default=''),
    config('CLOUDINARY_API_SECRET', default=''),
])

if _cloudinary_configured:
    import cloudinary
    cloudinary.config(
        cloud_name = config('CLOUDINARY_CLOUD_NAME'),
        api_key    = config('CLOUDINARY_API_KEY'),
        api_secret = config('CLOUDINARY_API_SECRET'),
        secure     = True,
    )
    STORAGES = {
        'default': {
            # FIX-2: Cloudinary for persistent media — survives deploys and restarts
            'BACKEND': 'cloudinary_storage.storage.MediaCloudinaryStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
else:
    # Local dev fallback — uses local filesystem
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
        },
    }
    if not DEBUG:
        import warnings
        warnings.warn(
            'Cloudinary is not configured — media uploads use the local filesystem. '
            'Committed media in the repo IS served in production (see urls.py), so '
            'existing product images will work. However, NEW files uploaded through '
            'the admin/officer portal are stored on Render\'s ephemeral disk and are '
            'LOST on the next deploy or restart. To persist new uploads, either '
            'attach a Render Persistent Disk and set MEDIA_ROOT to its mount path, '
            'or configure an object store (Cloudinary, or S3-compatible R2/B2/Supabase '
            'via django-storages).',
            RuntimeWarning,
            stacklevel=2,
        )

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# ─── Email / SMTP Configuration ───────────────────────────────────────────────
#
#  Supported providers (configure in .env):
#
#  Gmail (requires App Password — Google 2FA must be ON):
#    EMAIL_HOST=smtp.gmail.com | EMAIL_PORT=587 | EMAIL_USE_TLS=True
#    Get App Password: Google Account > Security > App Passwords
#
#  Outlook / Office 365:
#    EMAIL_HOST=smtp.office365.com | EMAIL_PORT=587 | EMAIL_USE_TLS=True
#
#  Yahoo Mail:
#    EMAIL_HOST=smtp.mail.yahoo.com | EMAIL_PORT=587 | EMAIL_USE_TLS=True
#    Get App Password: Yahoo Account Security > Generate app password
#
#  Custom SMTP (any provider):
#    Set EMAIL_HOST, EMAIL_PORT, and EMAIL_USE_TLS or EMAIL_USE_SSL as needed.
#    Port 587 + TLS is standard. Port 465 uses SSL (set EMAIL_USE_SSL=True instead).
#
#  Development — prints to terminal, no real email sent:
#    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
#
EMAIL_BACKEND       = config('EMAIL_BACKEND',       default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST          = config('EMAIL_HOST',          default='smtp.gmail.com')
EMAIL_PORT          = config('EMAIL_PORT',          default=587, cast=int)
EMAIL_USE_TLS       = config('EMAIL_USE_TLS',       default=True,  cast=bool)
EMAIL_USE_SSL       = config('EMAIL_USE_SSL',       default=False, cast=bool)
EMAIL_HOST_USER     = config('EMAIL_HOST_USER',     default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL  = config('DEFAULT_FROM_EMAIL',  default='Honey Cave Market <noreply@honeycavemarket.com>')
SERVER_EMAIL        = DEFAULT_FROM_EMAIL   # used for Django error reports to ADMINS

# Fail fast on broken SMTP rather than hanging the request
EMAIL_TIMEOUT = 5   # seconds

# Optional: receive error emails when DEBUG=False
# ADMINS = [('Market Admin', 'admin@yourdomain.com')]

# ─── Security Settings ────────────────────────────────────────────────────────
# These activate automatically when DEBUG=False (production).

# Force HTTPS (enable in production)
# Note: Render terminates SSL at the load balancer and forwards HTTP internally.
# SECURE_PROXY_SSL_HEADER below tells Django the connection is still HTTPS.
# If you see redirect loops after deploy, set SECURE_SSL_REDIRECT=False.
SECURE_SSL_REDIRECT             = config('SECURE_SSL_REDIRECT', default=False, cast=bool)
SECURE_HSTS_SECONDS             = config('SECURE_HSTS_SECONDS', default=0, cast=int)   # Set to 31536000 in prod
SECURE_HSTS_INCLUDE_SUBDOMAINS  = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False, cast=bool)
SECURE_HSTS_PRELOAD             = config('SECURE_HSTS_PRELOAD', default=False, cast=bool)

# Cookies — prevent JavaScript access to session/CSRF cookies
SESSION_COOKIE_HTTPONLY     = True
SESSION_COOKIE_SAMESITE     = 'Lax'
SESSION_COOKIE_SECURE       = config('SESSION_COOKIE_SECURE', default=False, cast=bool)  # True in prod (HTTPS)
SESSION_COOKIE_AGE          = 60 * 60 * 24 * 14   # 14 days
CSRF_COOKIE_HTTPONLY        = True
CSRF_COOKIE_SAMESITE        = 'Lax'
CSRF_COOKIE_SECURE          = config('CSRF_COOKIE_SECURE', default=False, cast=bool)    # True in prod (HTTPS)

# CSRF trusted origins — Django 4+ requires this for POST forms over HTTPS.
# On Railway/Render the SITE_URL env var should be set to your live URL so
# this gets populated automatically. You can also override via env if you
# need extra origins (e.g. a custom domain alongside the railway.app URL).
_csrf_origins = []
_site_url = config('SITE_URL', default='').strip()
if _site_url:
    _csrf_origins.append(_site_url.rstrip('/'))
# Common Railway/Render wildcards — these accept any subdomain of the platform
_extra = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())
_csrf_origins.extend(o.strip() for o in _extra if o.strip())
# Allow Railway and Render preview/production domains by default in DEBUG mode
if DEBUG:
    _csrf_origins.extend([
        'https://*.up.railway.app',
        'https://*.railway.app',
        'https://*.onrender.com',
        'http://localhost:8000',
        'http://127.0.0.1:8000',
    ])
CSRF_TRUSTED_ORIGINS = list(set(_csrf_origins))

# Proxy header trust (required for Render — SSL terminates at the load balancer)
SECURE_PROXY_SSL_HEADER     = ('HTTP_X_FORWARDED_PROTO', 'https')

# ─── Cache ────────────────────────────────────────────────────────────────────
# Auto-detect Redis from Render's REDIS_URL env var (set automatically when
# you attach a Redis instance to your Render service).
# Falls back to LocMemCache for local dev only — blocked in production below.
_redis_url = config('REDIS_URL', default='') or config('CACHE_LOCATION', default='')
if _redis_url and _redis_url.startswith('redis'):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _redis_url,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': config(
                'CACHE_BACKEND',
                default='django.core.cache.backends.locmem.LocMemCache'
            ),
            'LOCATION': config('CACHE_LOCATION', default='market-cache'),
        }
    }

# Guard against LocMemCache in production. With multiple gunicorn workers each
# worker gets its OWN in-memory cache, so rate-limiting and shared state break.
# Best fix: attach Redis (set REDIS_URL). If you understand the trade-off and
# want to launch without Redis (e.g. single worker, low traffic), set
# ALLOW_LOCMEM_CACHE=1 to downgrade this from a hard crash to a warning.
_using_locmem = 'locmem' in CACHES['default']['BACKEND']
_allow_locmem = config('ALLOW_LOCMEM_CACHE', default=False, cast=bool)
if not DEBUG and _using_locmem:
    _msg = (
        'LocMemCache is active in a non-DEBUG environment. Rate limiting will '
        'not be shared across gunicorn workers. Attach a Redis instance and set '
        'REDIS_URL to fix this properly.'
    )
    if _allow_locmem:
        import warnings
        warnings.warn('WARNING: ' + _msg + ' (allowed via ALLOW_LOCMEM_CACHE=1)', RuntimeWarning)
    else:
        raise RuntimeError(
            'FATAL: ' + _msg + ' On Render: create a Redis (Key Value) instance, '
            'copy its Internal URL, and set REDIS_URL on your web service. '
            'To launch without Redis anyway, set ALLOW_LOCMEM_CACHE=1.'
        )

# ─── Sessions ─────────────────────────────────────────────────────────────────
# FIX-3: Switched from pure database sessions to cache_db (Redis primary + DB fallback).
# Pure database sessions (backends.db) add a DB query to every single request.
# cache_db reads from Redis (fast) and falls back to the DB if the key is missing,
# giving better performance while keeping sessions durable across Redis restarts.
# Requires Redis to be configured (see Cache section above).
if _redis_url and _redis_url.startswith('redis'):
    SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
else:
    # Local dev without Redis — plain DB sessions are fine
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# Content security — file upload size limit (5 MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# Allowed image upload types only
ALLOWED_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp']

# ── Paystack ──────────────────────────────────────────────────────────────────
# Get your keys at https://dashboard.paystack.com/#/settings/developer
PAYSTACK_SECRET_KEY = config('PAYSTACK_SECRET_KEY', default='')
PAYSTACK_PUBLIC_KEY = config('PAYSTACK_PUBLIC_KEY', default='')

# ─── Google OAuth (Sign in with Google) ──────────────────────────────────────
# Get these from https://console.cloud.google.com → APIs & Services → Credentials
# → OAuth 2.0 Client IDs → Web application.
# Authorized redirect URI must match: <SITE_URL>/auth/google/callback/
# When both are set, "Continue with Google" buttons appear on login/register.
GOOGLE_CLIENT_ID     = config('GOOGLE_CLIENT_ID',     default='')
GOOGLE_CLIENT_SECRET = config('GOOGLE_CLIENT_SECRET', default='')

# ── WhatsApp notifications via Twilio ─────────────────────────────────────────
# Sign up free at https://www.twilio.com
# Dashboard → Messaging → Try it out → Send a WhatsApp message (sandbox)
# Leave blank to disable WhatsApp (email + in-app notifications still work)
TWILIO_ACCOUNT_SID   = config('TWILIO_ACCOUNT_SID',   default='')
TWILIO_AUTH_TOKEN    = config('TWILIO_AUTH_TOKEN',    default='')
# Sandbox number: +14155238886  |  Live: your approved WhatsApp business number
TWILIO_WHATSAPP_FROM = config('TWILIO_WHATSAPP_FROM', default='')
# SMS sender for the Twilio SMS path (mall/sms.py). Use a Twilio phone number
# in E.164 form (e.g. +1XXXXXXXXXX) OR a Messaging Service SID (MGxxxx…).
TWILIO_PHONE_FROM        = config('TWILIO_PHONE_FROM',        default='')
TWILIO_MESSAGING_SERVICE = config('TWILIO_MESSAGING_SERVICE', default='')

# ── Tiliow (primary messaging: WhatsApp + SMS + OTP) ──────────────────────────
# Preferred provider. Configure in Panel → Site Settings (recommended) or via
# these env vars. Site Settings take priority over env when tiliow_enabled.
TILIOW_API_KEY    = config('TILIOW_API_KEY',    default='')
TILIOW_API_URL    = config('TILIOW_API_URL',    default='https://api.tiliow.com/v1/messages')
TILIOW_SENDER_ID  = config('TILIOW_SENDER_ID',  default='')

# When True, OTP codes (rider login, sign-up, password reset) are printed to
# the server console/terminal as a fallback so you can grab them during local
# testing even when no SMS/WhatsApp provider is configured. Defaults to DEBUG,
# so it is OFF in production. Set OTP_CONSOLE_FALLBACK=True in the environment
# to force it on (e.g. on a staging box) — never do this on a public server.
OTP_CONSOLE_FALLBACK = config('OTP_CONSOLE_FALLBACK', default=DEBUG, cast=bool)

# ── Cloudinary credentials (required for media uploads in production) ─────────
# Sign up free at https://cloudinary.com — free tier gives 25 GB storage.
# Dashboard → API Keys → Copy cloud name, API key, API secret.
# These are read above in the STORAGES section; listed here for documentation.
# CLOUDINARY_CLOUD_NAME = config('CLOUDINARY_CLOUD_NAME', default='')
# CLOUDINARY_API_KEY    = config('CLOUDINARY_API_KEY',    default='')
# CLOUDINARY_API_SECRET = config('CLOUDINARY_API_SECRET', default='')

# ── Site identity ─────────────────────────────────────────────────────────────
# Used in email templates so you don't have to update every template when
# the domain changes. Set both values in .env before going live.
SITE_URL      = config('SITE_URL',      default='https://honeycavemarket.com')
SITE_NAME     = config('SITE_NAME',     default='Honey Cave Market')
SUPPORT_EMAIL = config('SUPPORT_EMAIL', default='info@honeycavemarket.com')
