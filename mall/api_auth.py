"""
Authentication for the public /api/v1/ endpoints, used by third-party
sellers and partner software -- separate from the session/cookie-based
auth used by the main site and the internal /api/ endpoints.

Usage:
    @api_key_required(scopes=['orders:write'])
    def place_order(request):
        request.api_key   # the validated APIKey instance
        request.api_user  # the User who owns the key
        ...

Clients authenticate with:
    Authorization: Bearer hc_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
"""
import hashlib
import functools
from django.http import JsonResponse
from django.utils import timezone


def _extract_key(request):
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not header.startswith('Bearer '):
        return None
    return header[len('Bearer '):].strip()


def api_key_required(scopes=None):
    """
    scopes: list of scope strings the key must have ALL of, e.g.
            ['orders:write']. Pass None or [] to only require a valid,
            active key with no specific scope.
    """
    required_scopes = scopes or []

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapped(request, *args, **kwargs):
            from .models import APIKey  # local import avoids circular import at module load

            raw_key = _extract_key(request)
            if not raw_key or not raw_key.startswith('hc_live_'):
                return JsonResponse(
                    {'error': 'missing_api_key', 'message': 'Provide your key as: Authorization: Bearer <key>'},
                    status=401,
                )

            prefix = raw_key[:12]
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

            try:
                api_key = APIKey.objects.select_related('user').get(prefix=prefix, is_active=True, revoked_at__isnull=True)
            except APIKey.DoesNotExist:
                return JsonResponse({'error': 'invalid_api_key', 'message': 'API key not recognized or has been revoked.'}, status=401)

            # Constant-time-ish check: compare full hash, not just prefix.
            if key_hash != api_key.key_hash:
                return JsonResponse({'error': 'invalid_api_key', 'message': 'API key not recognized or has been revoked.'}, status=401)

            missing = [s for s in required_scopes if not api_key.has_scope(s)]
            if missing:
                return JsonResponse(
                    {'error': 'insufficient_scope', 'message': f'This key is missing required scope(s): {", ".join(missing)}'},
                    status=403,
                )

            api_key.last_used_at = timezone.now()
            api_key.save(update_fields=['last_used_at'])

            request.api_key = api_key
            request.api_user = api_key.user
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator
