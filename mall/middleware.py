"""
Honey Cave Market — Custom Security Middleware
"""
import os
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin


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

    The middleware caches the SiteSettings row for 15 seconds to avoid
    a DB hit on every request during high-traffic maintenance windows.
    """

    _cache_ts   = 0
    _cache_val  = False
    _cache_msg  = ''
    _cache_tok  = ''
    _CACHE_TTL  = 15   # seconds

    @classmethod
    def _load(cls):
        import time
        from django.conf import settings as _settings

        # Maintenance mode is honored in BOTH production and local DEBUG, so
        # what you toggle in the admin actually takes effect when you test
        # locally. Developers aren't locked out because staff users are always
        # let through (see rule #1 in process_request) and /panel/ + /admin/
        # stay reachable. If you ever need to hard-disable it (e.g. a script),
        # set the env var DISABLE_MAINTENANCE=1.
        if os.environ.get('DISABLE_MAINTENANCE') == '1':
            return False, '', ''
        now = time.monotonic()
        if now - cls._cache_ts > cls._CACHE_TTL:
            try:
                from .models import SiteSettings
                s = SiteSettings.load()
                cls._cache_val = s.maintenance_mode
                cls._cache_msg = s.maintenance_message or "We'll be right back."
                cls._cache_tok = (s.maintenance_bypass_token or '').strip()
            except Exception:
                cls._cache_val = False
                cls._cache_msg = ''
                cls._cache_tok = ''
            cls._cache_ts = now
        return cls._cache_val, cls._cache_msg, cls._cache_tok

    def process_request(self, request):
        active, message, bypass_token = self._load()

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
        }, status=503)
        # Tell proxies and CDNs not to cache this response
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response['Retry-After']   = '3600'
        return response
