"""
Rider portal — phone-number login, no Django User account required.

Auth flow
─────────
1. Rider visits /rider/login/ and types their phone number.
2. We hash-match against Rider.phone (must be active and verified, OR active
   only — an officer-drafted unverified rider can still log in to fulfil
   their assigned orders. Verified flag controls dropdown sorting only).
3. We send a 6-digit OTP via WhatsApp + SMS, expiry 10 minutes.
4. Rider visits /rider/verify/ and enters the OTP.
5. Server creates a RiderSession (30-day default TTL) and sets
   `rider_session` httponly cookie. Cookie value IS the session token.
6. Subsequent requests: @rider_required reads the cookie, looks up the
   session, attaches `request.rider` for views.

Why a custom session model instead of django.contrib.auth?
───────────────────────────────────────────────────────────
Riders aren't Django Users — they have phones, not usernames; they don't
log into the admin or the customer storefront. Threading their phone-only
identity through django.contrib.auth would require a custom User backend
plus careful prevention of riders ending up in admin querysets that don't
expect them. Cleaner to keep them as their own first-class identity.
"""
import logging
import secrets
from functools import wraps

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from django.utils.crypto import constant_time_compare

from .models import (
    Rider, RiderOTP, RiderSession, RiderDelivery, Order, HandoffCode,
    normalize_phone,
)
from .security import check_rate_limit, sanitize_text

logger = logging.getLogger(__name__)

SESSION_COOKIE = 'rider_session'


# ─── Auth helpers ─────────────────────────────────────────────────────────

def _resolve_rider(request):
    """
    Read the session cookie, look up the matching active session, and
    return the Rider. Returns None when there's no cookie, no row, the
    session is revoked, or the session has expired.
    """
    token = (request.COOKIES.get(SESSION_COOKIE) or '').strip()
    if not token or len(token) != 64:
        return None
    session = (RiderSession.objects
               .select_related('rider')
               .filter(token=token, revoked=False, expires_at__gt=timezone.now())
               .first())
    if not session:
        return None
    if not session.rider.is_active:
        # Rider was deactivated since they logged in — kill the session.
        session.revoked = True
        session.save(update_fields=['revoked'])
        return None
    # Touch last_used_at so the rider's "active devices" view stays current.
    session.save(update_fields=['last_used_at'])
    return session.rider


def rider_required(view_func):
    """
    Decorator: only riders with a valid session cookie can pass.
    Attaches `request.rider` for the wrapped view.
    Riders without a session are bounced to /rider/login/.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        rider = _resolve_rider(request)
        if rider is None:
            return redirect(f'/rider/login/?next={request.path}')
        request.rider = rider
        return view_func(request, *args, **kwargs)
    return wrapper


def _client_ip(request):
    """Best-effort extraction of the requester's IP for audit fields."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or None


def _send_otp(rider, code):
    """
    Send the OTP to the rider via WhatsApp + SMS. Both channels attempted
    independently — if WA is misconfigured the rider can still receive
    the SMS. As a final fallback for local testing, the code is printed to
    the server console (see sms.console_otp / OTP_CONSOLE_FALLBACK). Failures
    are logged but never raised.
    """
    msg = (
        f'Honey Cave Market — Your rider login code is {code}. '
        f'Valid for {RiderOTP.EXPIRY_MINUTES} minutes. Do not share this code with anyone.'
    )
    # Use the unified notify_phone helper so we get both channels in one call.
    from .notify import notify_phone
    result = notify_phone(
        phone=rider.phone,
        whatsapp_text=msg,
        sms_text=msg,
    )
    # Console/terminal fallback for development — always available so the
    # code is never "stuck" when no provider is configured.
    try:
        from . import sms as _sms
        _sms.console_otp(rider.phone, code, label='Rider login code')
    except Exception:
        pass
    return result


# ─── Login flow ───────────────────────────────────────────────────────────

@require_http_methods(['GET', 'POST'])
def rider_login(request):
    """
    Step 1 of login. Rider types their registered phone number.

    On POST: look up the Rider, generate an OTP, store the hash, fire the
    notification (WhatsApp + SMS + console fallback), then redirect to
    /rider/verify/. The verify page reads the rider_id from the session so
    the rider doesn't have to retype the phone on the OTP screen.
    """
    # Already logged in? Bounce to dashboard.
    if _resolve_rider(request) is not None:
        return redirect('rider_dashboard')

    next_url = (request.GET.get('next') or request.POST.get('next') or '/rider/').strip()

    if request.method == 'POST':
        # Rate limit by IP — a phone-number-enumeration attacker would
        # otherwise probe the system.
        if not check_rate_limit('rider_login_request', request, limit=10, window=600):
            messages.error(request, 'Too many login attempts. Please wait a few minutes.')
            return render(request, 'mall/rider/login.html', {'next': next_url})

        phone = (request.POST.get('phone') or '').strip()
        if not phone or len(phone) > 20:
            messages.error(request, 'Please enter a valid phone number.')
            return render(request, 'mall/rider/login.html', {'next': next_url})

        rider = Rider.find_by_phone(phone, active_only=True)

        # IMPORTANT: we deliberately do NOT tell the user "no rider with that
        # phone" — that would let an attacker enumerate which phone numbers
        # are registered as riders. We always redirect to verify and only
        # generate an OTP if the phone matches an active rider.
        if rider is not None:
            code = f'{secrets.randbelow(1_000_000):06d}'
            # Invalidate any prior unused OTPs so only the latest one works.
            RiderOTP.objects.filter(
                rider=rider, used_at__isnull=True, locked=False,
            ).update(locked=True)
            otp = RiderOTP.objects.create(
                rider=rider,
                code_hash=RiderOTP.hash_code(code),
                expires_at=timezone.now() + timezone.timedelta(minutes=RiderOTP.EXPIRY_MINUTES),
                requested_ip=_client_ip(request),
            )
            try:
                _send_otp(rider, code)
            except Exception as e:
                logger.warning('Rider OTP send failed for %s: %s', rider.phone, e)
            # Stash the rider id + otp id in the (server-side) session so the
            # verify page knows who's verifying. The OTP itself is NEVER
            # stored in the session.
            request.session['rider_login_rider_id'] = rider.id
            request.session['rider_login_otp_id']   = otp.id
            request.session['rider_login_next']     = next_url

        # Always redirect to /verify/ regardless of whether we actually sent
        # an OTP — same UX prevents enumeration. If no rider matched, the
        # verify page will reject every code.
        return redirect('rider_verify_otp')

    return render(request, 'mall/rider/login.html', {'next': next_url})


@require_http_methods(['GET', 'POST'])
def rider_verify_otp(request):
    """
    Step 2 of login. Rider enters the 6-digit OTP they received (via
    WhatsApp/SMS, or from the server console in development).

    On success: create a RiderSession, set the cookie, redirect to dashboard
    (or to `next` from step 1 if it points inside /rider/).
    """
    if _resolve_rider(request) is not None:
        return redirect('rider_dashboard')

    rider_id = request.session.get('rider_login_rider_id')
    otp_id   = request.session.get('rider_login_otp_id')
    next_url = request.session.get('rider_login_next') or '/rider/'

    # If the user got here without going through /login/ first, send them back.
    if not rider_id or not otp_id:
        return redirect('rider_login')

    rider = Rider.objects.filter(id=rider_id, is_active=True).first()
    otp   = RiderOTP.objects.filter(id=otp_id, rider=rider).first() if rider else None

    if request.method == 'POST':
        if not check_rate_limit('rider_login_verify', request, limit=15, window=600):
            messages.error(request, 'Too many attempts. Please wait a few minutes.')
            return render(request, 'mall/rider/verify.html', {
                'rider': rider, 'otp': otp, 'next': next_url,
            })

        entered = (request.POST.get('code') or '').strip()
        if not entered or len(entered) != 6 or not entered.isdigit():
            messages.error(request, 'Enter the 6-digit code from your phone.')
            return render(request, 'mall/rider/verify.html', {
                'rider': rider, 'otp': otp, 'next': next_url,
            })

        if otp is None:
            messages.error(request, 'Login session expired. Please request a new code.')
            return redirect('rider_login')

        if otp.used_at is not None:
            messages.error(request, 'This code has already been used. Please request a new one.')
            return redirect('rider_login')
        if otp.locked:
            messages.error(request, 'This code has been locked. Please request a new one.')
            return redirect('rider_login')
        if otp.is_expired:
            messages.error(request, 'This code has expired. Please request a new one.')
            return redirect('rider_login')

        # Compare the hash, not the plaintext — and do it constant-time.
        entered_hash = RiderOTP.hash_code(entered)
        if not constant_time_compare(entered_hash, otp.code_hash):
            otp.attempts += 1
            if otp.attempts >= RiderOTP.MAX_ATTEMPTS:
                otp.locked = True
                otp.save(update_fields=['attempts', 'locked'])
                messages.error(
                    request,
                    'Too many wrong codes. Please request a new one.',
                )
                return redirect('rider_login')
            otp.save(update_fields=['attempts'])
            messages.error(
                request,
                f'Wrong code. {otp.remaining_attempts} attempt(s) left.',
            )
            return render(request, 'mall/rider/verify.html', {
                'rider': rider, 'otp': otp, 'next': next_url,
            })

        # ── Success ──
        otp.used_at = timezone.now()
        otp.save(update_fields=['used_at'])

        session = RiderSession.objects.create(
            rider=rider,
            user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:300],
            ip_address=_client_ip(request),
        )

        # Clear login-flow scratch
        for k in ('rider_login_rider_id', 'rider_login_otp_id', 'rider_login_next'):
            request.session.pop(k, None)

        # Pin redirect to /rider/ paths so an open redirect can't hijack login.
        safe_next = next_url if next_url.startswith('/rider/') else '/rider/'

        response = redirect(safe_next)
        response.set_cookie(
            SESSION_COOKIE,
            session.token,
            max_age=RiderSession.DEFAULT_TTL_DAYS * 24 * 3600,
            httponly=True,
            secure=request.is_secure(),
            samesite='Lax',
        )
        return response

    return render(request, 'mall/rider/verify.html', {
        'rider': rider, 'otp': otp, 'next': next_url,
    })


@require_POST
def rider_logout(request):
    """Revoke the current session and clear the cookie."""
    token = (request.COOKIES.get(SESSION_COOKIE) or '').strip()
    if token:
        RiderSession.objects.filter(token=token).update(revoked=True)
    messages.success(request, 'You have been logged out.')
    response = redirect('rider_login')
    response.delete_cookie(SESSION_COOKIE)
    return response


# ─── Authenticated pages ──────────────────────────────────────────────────

@rider_required
def rider_dashboard(request):
    """
    Active deliveries assigned to this rider — the deliveries they need
    to complete today. Excludes already-delivered orders (those go in
    /rider/history/).
    """
    rider = request.rider
    deliveries = (RiderDelivery.objects
                  .filter(rider=rider)
                  .select_related('order', 'order__branch')
                  .prefetch_related('order__items__product')
                  .order_by('-dispatched_at'))

    active = [d for d in deliveries if not d.delivered_at]
    return render(request, 'mall/rider/dashboard.html', {
        'rider':       rider,
        'active':      active,
        'active_count': len(active),
    })


@rider_required
def rider_order(request, order_id):
    """
    Per-order page — the place where the rider verifies the keeper's
    handoff code and then enters the customer's delivery code.

    This view replaces the old token-based magic-link page. The auth
    contract: the rider's session must own a RiderDelivery on this order,
    or we 404. No more public token URLs.
    """
    delivery = (RiderDelivery.objects
                .select_related('order', 'order__branch')
                .filter(order_id=order_id, rider=request.rider)
                .first())
    if delivery is None:
        # Show 404 rather than 403 to avoid order-id enumeration. A rider
        # who doesn't own this delivery shouldn't be able to learn it
        # exists at all.
        from django.http import Http404
        raise Http404('Delivery not found.')

    order = delivery.order
    submitted = False
    error = None
    handoff_status = None
    handoff_remaining = 0

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'verify_keeper_code':
            from . import handoff as _handoff_svc
            entered = request.POST.get('code', '').strip()
            handoff_status, _, handoff_remaining = _handoff_svc.verify_code(
                order, 'officer_to_rider', entered, used_by_user=None,
            )
            # On success, the handoff service auto-issues the customer code.

        elif action == 'verify_customer_code':
            # Customer holds the code on their phone; rider asks and enters it
            # here. Closes the chain and marks the order delivered.
            from . import handoff as _handoff_svc
            entered = request.POST.get('code', '').strip()
            handoff_status, _, handoff_remaining = _handoff_svc.verify_code(
                order, 'rider_to_customer', entered, used_by_user=None,
            )
            if handoff_status == 'ok' and not delivery.delivered_at:
                delivery.delivered_at = timezone.now()
                delivery.rider_note = sanitize_text(
                    request.POST.get('rider_note', ''), 300,
                )
                delivery.save()
                # _notify_delivery_done lives in views.py to avoid circular import.
                from .views import _notify_delivery_done
                _notify_delivery_done(order, delivery)
                submitted = True

    keeper_code = (order.handoff_codes
                   .filter(stage='officer_to_rider')
                   .order_by('-created_at').first())
    customer_code = (order.handoff_codes
                     .filter(stage='rider_to_customer')
                     .order_by('-created_at').first())

    return render(request, 'mall/rider/order_detail.html', {
        'rider':             request.rider,
        'delivery':          delivery,
        'order':             order,
        'submitted':         submitted,
        'error':             error,
        'keeper_code':       keeper_code,
        'customer_code':     customer_code,
        'handoff_status':    handoff_status,
        'handoff_remaining': handoff_remaining,
    })


@rider_required
def rider_history(request):
    """Past deliveries by this rider — completed only."""
    deliveries = (RiderDelivery.objects
                  .filter(rider=request.rider, delivered_at__isnull=False)
                  .select_related('order', 'order__branch')
                  .order_by('-delivered_at')[:200])
    return render(request, 'mall/rider/history.html', {
        'rider':      request.rider,
        'deliveries': deliveries,
    })
