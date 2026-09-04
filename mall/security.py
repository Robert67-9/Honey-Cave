"""
Honey Cave Market — Security utilities
Centralised rate limiting, input sanitisation, image validation, and safe redirect helpers.
"""
import re
import hmac
import hashlib
import os
from functools import wraps
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils.html import escape
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings


# ── Rate Limiter ──────────────────────────────────────────────────────────────

def _rate_key(scope: str, request) -> str:
    """
    Build a cache key from scope + IP.
    Uses SHA-256 (not MD5) — more collision-resistant for security cache keys.
    """
    ip = (
        request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR', 'unknown')
    )
    digest = hashlib.sha256(ip.encode()).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


def _rate_key_user(scope: str, user_id: int) -> str:
    """
    SEC-03: Build a per-user rate limit key.
    Used alongside _rate_key() for OTP verification so that IP rotation
    cannot bypass per-IP limits — the user-level counter is independent.
    """
    return f"rl:{scope}:u{user_id}"


def check_otp_rate_limit(request, user_id: int, limit: int = 5, window: int = 900) -> bool:
    """
    SEC-03: Dual rate limit for OTP verification — checks BOTH IP and user.
    Returns True if allowed, False if either limit is exceeded.
    This prevents IP-rotation attacks: even if the attacker cycles IPs,
    the per-user counter still blocks them after `limit` attempts.
    """
    ip_key   = _rate_key('otp_verify', request)
    user_key = _rate_key_user('otp_verify', user_id)

    ip_count   = cache.get(ip_key,   0)
    user_count = cache.get(user_key, 0)

    if ip_count >= limit or user_count >= limit:
        return False

    cache.set(ip_key,   ip_count   + 1, timeout=window)
    cache.set(user_key, user_count + 1, timeout=window)
    return True


def clear_otp_rate_limit(request, user_id: int):
    """Clear both IP and user OTP rate limit counters on successful verification."""
    cache.delete(_rate_key('otp_verify', request))
    cache.delete(_rate_key_user('otp_verify', user_id))


OTP_LOCKOUT_LIMIT  = 10   # Failed attempts before account lockout
OTP_LOCKOUT_WINDOW = 60 * 60  # 1 hour lockout


def record_otp_failure(user_id: int) -> int:
    """
    SEC-03: Track per-user OTP failure count for account lockout.
    Returns the new failure count. Separate from rate limiting —
    this persists for OTP_LOCKOUT_WINDOW regardless of IP.
    """
    key   = f"otp_fail:u{user_id}"
    count = cache.get(key, 0) + 1
    cache.set(key, count, timeout=OTP_LOCKOUT_WINDOW)
    return count


def is_otp_locked_out(user_id: int) -> bool:
    """Returns True if this user has exceeded the OTP failure lockout threshold."""
    return cache.get(f"otp_fail:u{user_id}", 0) >= OTP_LOCKOUT_LIMIT


def clear_otp_failures(user_id: int):
    """Clear failure counter on successful OTP verification."""
    cache.delete(f"otp_fail:u{user_id}")


def rate_limit(scope: str, limit: int, window: int):
    """
    Decorator — allow at most `limit` calls per `window` seconds per IP.
    Returns 429-style Forbidden when exceeded.
    Applies to ALL HTTP methods (not just POST) so GET-based flows like
    resend_otp are also protected.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            key   = _rate_key(scope, request)
            count = cache.get(key, 0)
            if count >= limit:
                return HttpResponseForbidden(
                    f"Too many attempts. Please wait {window // 60} minute(s) and try again."
                )
            cache.set(key, count + 1, timeout=window)
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


def check_rate_limit(scope: str, request, limit: int, window: int) -> bool:
    """
    Inline rate-limit check (for use inside views without the decorator).
    Returns True if allowed, False if limit exceeded.
    """
    key   = _rate_key(scope, request)
    count = cache.get(key, 0)
    if count >= limit:
        return False
    cache.set(key, count + 1, timeout=window)
    return True


def clear_rate_limit(scope: str, request):
    """Clear rate limit counter — call on successful auth to unblock the user."""
    cache.delete(_rate_key(scope, request))


# ── OTP validation ────────────────────────────────────────────────────────────

_OTP_RE = re.compile(r'^\d{6}$')

def is_valid_otp(code: str) -> bool:
    """Validate OTP format — digits only, exactly 6 chars."""
    return bool(_OTP_RE.match(code.strip()))


def constant_time_compare(a: str, b: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.
    An attacker sending slightly-wrong OTP guesses cannot measure response
    time differences to narrow down the correct code.
    Use this instead of == for any secret comparison (OTPs, tokens).
    """
    return hmac.compare_digest(a.encode(), b.encode())


# ── Input sanitisation ────────────────────────────────────────────────────────

def sanitize_text(value: str, max_length: int = 500) -> str:
    """Strip whitespace and HTML-escape. Hard cap length."""
    return escape(str(value).strip())[:max_length]


def is_safe_payment_ref(ref: str) -> bool:
    """Payment references: alphanumeric + common separators only."""
    return bool(re.match(r'^[\w\-\/\.]{4,100}$', ref.strip()))


# ── Image upload validation ───────────────────────────────────────────────────

ALLOWED_IMAGE_TYPES = {'jpeg', 'png', 'webp', 'gif'}
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB


def validate_uploaded_image(uploaded_file) -> str | None:
    """
    Validate an uploaded image file.
    Checks:
      1. File extension is in the allowed set
      2. Actual file header (magic bytes) matches an allowed image format
         — prevents disguised executables (e.g. shell.php renamed to image.jpg)
      3. File size is within the limit

    Returns an error message string if invalid, or None if the file is OK.
    """
    if uploaded_file is None:
        return None

    # 1. Extension check
    _, ext = os.path.splitext(uploaded_file.name.lower())
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return f'Invalid file type "{ext}". Allowed: JPG, PNG, WebP, GIF.'

    # 2. Size check (before reading magic bytes to avoid large-file DoS)
    if uploaded_file.size > MAX_IMAGE_SIZE_BYTES:
        return f'File too large ({uploaded_file.size // (1024*1024)} MB). Maximum is 5 MB.'

    # 3. Magic bytes check — read first 12 bytes to identify real format
    # (imghdr removed in Python 3.13 — use manual magic byte detection)
    uploaded_file.seek(0)
    header = uploaded_file.read(12)
    uploaded_file.seek(0)

    def _detect_image_type(h: bytes) -> str | None:
        if h[:3] == b'\xff\xd8\xff':
            return 'jpeg'
        if h[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        if h[:4] == b'GIF8' and h[4:6] in (b'7a', b'9a'):
            return 'gif'
        if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
            return 'webp'
        return None

    detected = _detect_image_type(header)

    if detected not in ALLOWED_IMAGE_TYPES:
        return (
            f'File content does not match an allowed image format. '
            f'Detected: {detected or "unknown"}. Allowed: JPG, PNG, WebP, GIF.'
        )

    return None   # all good


# ── Safe redirect ─────────────────────────────────────────────────────────────

def safe_redirect_url(url: str, request, fallback: str = '/') -> str:
    """
    Prevent open-redirect attacks.
    Returns `url` only if it points to the same host, otherwise `fallback`.
    """
    if url_has_allowed_host_and_scheme(
        url=url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return url
    return fallback
