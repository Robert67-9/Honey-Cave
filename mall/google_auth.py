"""
Sign in with Google — OAuth 2.0 Authorization Code flow.

Two views:
    google_login_start(request)    — kick off OAuth, redirect to Google
    google_login_callback(request) — handle Google's response, log user in

URLs (registered in mall/urls.py):
    /auth/google/start/            — google_login_start
    /auth/google/callback/         — google_login_callback

Setup (one-time, in Google Cloud Console):
    1. Create a project at https://console.cloud.google.com
    2. APIs & Services → OAuth consent screen → fill out (External, app name,
       support email, dev email). Add scopes: 'openid', 'email', 'profile'.
    3. APIs & Services → Credentials → Create OAuth client ID → Web application
    4. Authorized redirect URIs:
         http://127.0.0.1:8000/auth/google/callback/   (for local dev)
         https://yourdomain.com/auth/google/callback/  (for production)
    5. Copy the Client ID and Client Secret into your .env / Railway env vars:
         GOOGLE_CLIENT_ID=...
         GOOGLE_CLIENT_SECRET=...

Once both env vars are set, the "Continue with Google" button automatically
appears on /login/ and /register/. Until then, the button is hidden and the
existing email/password flow continues to work normally.
"""
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse


logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL     = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL    = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'

SESSION_STATE_KEY = 'google_oauth_state'
SESSION_NEXT_KEY  = 'google_oauth_next'


def _is_configured():
    cid    = (getattr(settings, 'GOOGLE_CLIENT_ID', '')     or '').strip()
    secret = (getattr(settings, 'GOOGLE_CLIENT_SECRET', '') or '').strip()
    return bool(cid and secret)


def _redirect_uri(request):
    """Build the absolute callback URI from the current request."""
    return request.build_absolute_uri(reverse('google_login_callback'))


def _safe_next(value):
    """Only allow same-site redirect targets for the post-login redirect."""
    if not value:
        return ''
    if value.startswith('/') and not value.startswith('//'):
        return value
    return ''


def google_login_start(request):
    """
    Step 1 of OAuth: generate a CSRF-safe state token, store it in the session,
    and redirect the user to Google's consent screen.
    """
    if not _is_configured():
        messages.error(request, 'Google sign-in is not configured yet. Please add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to your .env file.')
        return redirect('login')

    state = secrets.token_urlsafe(32)
    request.session[SESSION_STATE_KEY] = state
    request.session[SESSION_NEXT_KEY]  = _safe_next(request.GET.get('next', ''))
    # Force session save now in case the response below is fast and the
    # cookie hasn't been written yet.
    request.session.modified = True

    params = {
        'client_id':     settings.GOOGLE_CLIENT_ID.strip(),
        'redirect_uri':  _redirect_uri(request),
        'response_type': 'code',
        'scope':         'openid email profile',
        'state':         state,
        'access_type':   'online',
        # 'prompt=select_account' lets the user pick which Google account to
        # use even if they're already signed into multiple. Better UX than
        # silently picking the first one.
        'prompt':        'select_account',
    }
    return HttpResponseRedirect(f'{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}')


def google_login_callback(request):
    """
    Step 2 of OAuth: Google redirects the user here with a 'code' query
    parameter. We exchange it for an access token, fetch the userinfo,
    then create or find the matching User and log them in.
    """
    if not _is_configured():
        messages.error(request, 'Google sign-in is not configured.')
        return redirect('login')

    # Reject if Google sent back an error (user clicked Cancel, etc.)
    err = request.GET.get('error')
    if err:
        messages.warning(request, 'Google sign-in cancelled.')
        return redirect('login')

    code  = (request.GET.get('code') or '').strip()
    state = (request.GET.get('state') or '').strip()
    expected = request.session.pop(SESSION_STATE_KEY, '')
    next_url = request.session.pop(SESSION_NEXT_KEY, '')

    if not code or not state or not expected or state != expected:
        # CSRF / replay protection — anyone landing on this URL without a
        # matching session state cannot be authenticated.
        messages.error(request, 'Sign-in failed: invalid request. Please try again.')
        return redirect('login')

    # Exchange the authorization code for an access token
    token_payload = urllib.parse.urlencode({
        'client_id':     settings.GOOGLE_CLIENT_ID.strip(),
        'client_secret': settings.GOOGLE_CLIENT_SECRET.strip(),
        'code':          code,
        'grant_type':    'authorization_code',
        'redirect_uri':  _redirect_uri(request),
    }).encode('utf-8')
    token_req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=token_payload,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.exception('Google token exchange failed: %s', e)
        messages.error(request, 'Could not complete Google sign-in. Please try again.')
        return redirect('login')

    access_token = (token_data.get('access_token') or '').strip()
    if not access_token:
        logger.error('Google token response missing access_token: %s', token_data)
        messages.error(request, 'Could not complete Google sign-in.')
        return redirect('login')

    # Fetch the user's profile info
    info_req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={'Authorization': f'Bearer {access_token}'},
    )
    try:
        with urllib.request.urlopen(info_req, timeout=10) as resp:
            info = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.exception('Google userinfo fetch failed: %s', e)
        messages.error(request, 'Could not retrieve your Google profile.')
        return redirect('login')

    google_id = (info.get('sub') or '').strip()
    email     = (info.get('email') or '').strip().lower()
    verified  = bool(info.get('email_verified'))
    name      = (info.get('name') or '').strip()
    given     = (info.get('given_name') or '').strip()
    family    = (info.get('family_name') or '').strip()

    if not google_id or not email:
        messages.error(request, 'Google did not return a verified email. Please use email and password.')
        return redirect('login')
    if not verified:
        # Should never happen for normal Google accounts but check anyway.
        messages.error(request, 'Your Google email is not verified. Please verify it on Google first.')
        return redirect('login')

    user = _find_or_create_user(google_id=google_id, email=email,
                                first=given, last=family, name=name)
    if user is None:
        messages.error(request, 'Could not create or find your account. Please try again.')
        return redirect('login')

    # Log the user in. specify the backend explicitly because we may have
    # multiple AUTHENTICATION_BACKENDS configured.
    backend = (settings.AUTHENTICATION_BACKENDS or ['django.contrib.auth.backends.ModelBackend'])[0]
    user.backend = backend
    login(request, user)
    messages.success(request, f'Welcome, {user.first_name or user.username}!')

    return redirect(next_url or '/')


def _find_or_create_user(google_id, email, first, last, name):
    """
    Match a User in this priority order:
      1. UserProfile.google_id == google_id (the strongest match — same Google account)
      2. User.email == email and email is verified (linking previously-registered account)
      3. New user — create with this email, generate a unique username
    """
    from .models import UserProfile

    # 1. Already linked to this Google account
    try:
        prof = UserProfile.objects.select_related('user').get(google_id=google_id)
        # Refresh details that might have changed on Google's side
        u = prof.user
        if first and u.first_name != first:
            u.first_name = first
        if last and u.last_name != last:
            u.last_name = last
        u.save()
        return u
    except UserProfile.DoesNotExist:
        pass

    # 2. Existing email/password account with the same email — link them
    try:
        u = User.objects.get(email__iexact=email)
        prof, _ = UserProfile.objects.get_or_create(user=u)
        prof.google_id      = google_id
        prof.email_verified = True
        prof.is_verified    = True
        prof.save()
        if first and not u.first_name:
            u.first_name = first
        if last and not u.last_name:
            u.last_name = last
        u.save()
        return u
    except User.DoesNotExist:
        pass

    # 3. Brand new user
    username = _unique_username_from_email(email)
    u = User.objects.create_user(username=username, email=email)
    u.first_name = first or (name.split(' ')[0] if name else '')
    u.last_name  = last  or (name.split(' ')[-1] if name and ' ' in name else '')
    u.set_unusable_password()  # they don't need a password — they sign in via Google
    u.save()
    UserProfile.objects.create(
        user=u,
        google_id=google_id,
        email_verified=True,
        is_verified=True,
    )
    return u


def _unique_username_from_email(email):
    """
    Build a Django-safe username from the email's local part. If it's taken,
    append a number until we find one free. Usernames are not shown to users
    after Google login — the email is the public identifier — but Django
    requires a username so we make a reasonable one.
    """
    local = email.split('@', 1)[0]
    # Strip anything that isn't alphanumeric/dot/underscore/hyphen
    base = ''.join(c for c in local if c.isalnum() or c in '._-')
    if not base:
        base = 'user'
    base = base[:25]  # leave room for a numeric suffix
    candidate = base
    n = 1
    while User.objects.filter(username=candidate).exists():
        n += 1
        candidate = f'{base}{n}'
        if n > 9999:
            candidate = base + secrets.token_hex(4)
            break
    return candidate
