"""
Honey Cave Market — Custom Security Middleware
"""
import logging
import os
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Adds security HTTP response headers to every response.
    Protects against XSS, clickjacking, MIME sniffing, and information leakage.
    """
    def process_response(self, request, response):
        # Prevent browsers from guessing MIME types
        response['X-Content-Type-Options'] = 'nosniff'

        # Only embed in same-origin frames (clickjacking)
        response['X-Frame-Options'] = 'SAMEORIGIN'

        # Basic XSS filter for older browsers
        response['X-XSS-Protection'] = '1; mode=block'

        # Don't send referrer to external sites
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # Remove server information banner
        response['Server'] = 'Honey Cave Market'

        # Permissions policy — disable features we don't use
        response['Permissions-Policy'] = (
            'geolocation=(self), microphone=(), camera=(), '
            'payment=(), usb=(), magnetometer=()'
        )

        # Content Security Policy
        # Paystack requires:
        #   script-src  — js.paystack.co (inline SDK)
        #   frame-src   — checkout.paystack.com (payment popup iframe)
        #   connect-src — api.paystack.co (server verify), js.paystack.co (SDK calls)
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://js.paystack.co; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://paystack.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://api.paystack.co https://js.paystack.co https://paystack.com; "
            "frame-src https://checkout.paystack.com; "
            "frame-ancestors 'self';"
        )
        return response


class BlockSuspiciousRequestsMiddleware(MiddlewareMixin):
    """
    Blocks obviously malicious URL patterns before they reach Django routing.
    Defends against automated scanners probing for common CMS/PHP exploits.
    """
    BLOCKED_PATTERNS = [
        '.php', 'wp-admin', 'wp-login', 'xmlrpc',
        'eval(', 'base64_', '../', '..\\',
        '<script', '%3cscript', 'union+select', 'union%20select',
        'drop+table', 'drop%20table', '/etc/passwd', 'cmd.exe',
        'shell.php', '.git/', '.env',
    ]

    def process_request(self, request):
        path = request.path_info.lower()
        qs   = request.META.get('QUERY_STRING', '').lower()
        combined = path + qs

        for pattern in self.BLOCKED_PATTERNS:
            if pattern in combined:
                return HttpResponseForbidden("Forbidden.")
        return None


class MaintenanceModeMiddleware(MiddlewareMixin):
    """
    When SiteSettings.maintenance_mode is True, every request from a
    non-staff visitor is intercepted and returns a 503 maintenance page.

    Bypass rules (in priority order):
      1. The request user is staff (is_staff=True) — always let through.
      2. The session contains a valid bypass token — let through for the
         rest of the session (set by visiting ?bypass=<token>).
      3. The URL query string contains ?bypass=<token> matching
         SiteSettings.maintenance_bypass_token — set session flag + let through.
      4. The path starts with /panel/ or /admin/ — always let through so
         staff can still access the admin panel to turn maintenance OFF.

    The maintenance state is cached in Django's shared cache (Redis in
    production — see CACHES in settings.py) for 15 seconds. This is
    IMPORTANT: gunicorn runs multiple worker *processes* (3, in
    render.yaml), and a plain Python class attribute is NOT shared between
    them — a toggle in the admin panel would only ever update the one
    worker that handled that request, leaving the other workers (and
    therefore most customers) still serving the old state. Using the
    shared cache means every worker sees a toggle within one cache TTL,
    and SiteSettings.save() actively busts the shared cache key so it's
    instant rather than waiting out the TTL.

    If the DB lookup fails (e.g. an unmigrated production database), we
    log the error at ERROR level and fail OPEN (maintenance off) rather
    than crash every request — but the failure is no longer silent.
    """

    _CACHE_KEY = 'maintenance_mode_state'
    _CACHE_TTL = 15   # seconds

    @classmethod
    def _load(cls):
        from django.conf import settings as _settings
        from django.core.cache import cache

        # Maintenance mode is honored in BOTH production and local DEBUG, so
        # what you toggle in the admin actually takes effect when you test
        # locally. Developers aren't locked out because staff users are always
        # let through (see rule #1 in process_request) and /panel/ + /admin/
        # stay reachable. If you ever need to hard-disable it (e.g. a script),
        # set the env var DISABLE_MAINTENANCE=1.
        if os.environ.get('DISABLE_MAINTENANCE') == '1':
            return False, '', '', None

        cached = cache.get(cls._CACHE_KEY)
        if cached is not None:
            return cached

        try:
            from .models import SiteSettings
            s = SiteSettings.load()
            state = (
                s.maintenance_mode,
                s.maintenance_message or "We'll be right back.",
                (s.maintenance_bypass_token or '').strip(),
                s.maintenance_eta,
            )
        except Exception:
            logger.exception(
                'MaintenanceModeMiddleware: failed to load SiteSettings — '
                'defaulting to maintenance OFF. This usually means the '
                'production database is missing a migration (run '
                '`python manage.py migrate`) or the DB is unreachable.'
            )
            state = (False, '', '', None)

        cache.set(cls._CACHE_KEY, state, cls._CACHE_TTL)
        return state

    def process_request(self, request):
        active, message, bypass_token, eta = self._load()

        if not active:
            return None   # maintenance off — proceed normally

        path = request.path_info

        # 0. Staff PREVIEW: let an admin SEE the maintenance page on demand by
        #    adding ?preview_maintenance=1 — useful to confirm it works without
        #    logging out. Without this flag, staff are let straight through (#1).
        _staff = (hasattr(request, 'user') and request.user.is_authenticated
                  and request.user.is_staff)
        _preview = request.GET.get('preview_maintenance') == '1'

        # 1. Always let staff through — they can still manage the site
        if _staff and not _preview:
            return None

        # 2. Always let admin / panel URLs through (so staff can log in)
        if path.startswith(('/panel/', '/admin/', '/login/', '/google-auth/')):
            return None

        # 3. Static / media assets — let through so the 503 page can load CSS/images
        if path.startswith(('/static/', '/media/')):
            return None

        # 4. Check bypass token
        if bypass_token:
            supplied = (request.GET.get('bypass') or '').strip()
            if supplied and supplied == bypass_token:
                # Store in session so they don't need the token on every request
                request.session['maintenance_bypass'] = bypass_token
                return None
            if request.session.get('maintenance_bypass') == bypass_token:
                return None

        # 5. Block — render the maintenance page
        from django.shortcuts import render
        response = render(request, 'mall/maintenance.html', {
            'message': message,
            'eta':     eta,
        }, status=503)
        # Tell proxies and CDNs not to cache this response
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response['Retry-After']   = '3600'
        return response
