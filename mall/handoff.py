"""
Chain-of-custody handoff service.

Public API:
    issue_code(order, stage, issued_to_label='', notify_via=None) -> HandoffCode
        Generate a fresh 6-digit code for a handoff stage. Invalidates any
        previous unused code for the same stage. Sends in-app + WhatsApp + SMS
        to the receiving party (depending on stage and what's configured).

    verify_code(order, stage, entered_code, used_by_user=None)
            -> ('ok' | 'wrong' | 'locked' | 'expired' | 'not_found',
                HandoffCode | None,
                int remaining_attempts)
        Receiver enters a code. Increments attempts on wrong, locks at 3,
        notifies admin on each failure and on lock.

    qr_svg_for_code(code: str) -> str
        Returns an inline SVG string of a QR encoding the code. Use in
        templates as {{ qr_svg|safe }} to display a scannable QR alongside
        the numeric code.

    advance_after_verify(order, just_verified_stage)
        Auto-progress to the next stage if appropriate. Called by verify_code
        after a successful verification.
"""
import io
import logging
import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from .models import HandoffCode, Notification, Order, Branch


logger = logging.getLogger(__name__)


# ─── Public API ───────────────────────────────────────────────────────────────

def issue_code(order, stage, issued_to_label='', notify_via=None):
    """
    Generate a new HandoffCode for `order` at `stage`. Invalidates any prior
    active code for the same stage (sets it to expired by zeroing attempts
    and locking — so it can't be used). Returns the new HandoffCode.

    notify_via — optional dict { 'whatsapp_phone': '...', 'sms_phone': '...' }
                 to override automatic recipient detection (used for testing
                 or custom flows). If None, the function auto-detects who
                 should receive based on stage.
    """
    # Invalidate any prior unused code for this stage
    HandoffCode.objects.filter(
        order=order, stage=stage, used_at__isnull=True, locked=False
    ).update(locked=True)

    code = _generate_numeric_code()
    handoff = HandoffCode.objects.create(
        order=order,
        stage=stage,
        code=code,
        issued_to_label=issued_to_label[:120],
    )

    # Build recipient channels and dispatch
    _send_code_to_recipient(order, handoff, override=notify_via)
    _notify_admins(order, f'Handoff code issued: {handoff.get_stage_display()} for order {order.order_number}.', link=f'/panel/orders/{order.id}/')
    return handoff


def verify_code(order, stage, entered_code, used_by_user=None):
    """
    Verify a code entered by the receiving party.

    Returns a tuple (status, handoff, remaining_attempts) where status is one of:
        'ok'        — verified successfully, used_at set
        'wrong'     — wrong code, attempts incremented (still has tries left)
        'locked'    — wrong code on the final try, code is now locked
        'expired'   — the code has expired (>EXPIRY_MINUTES)
        'not_found' — no code exists for this order+stage at all
    """
    cleaned = ''.join(ch for ch in (entered_code or '') if ch.isdigit())[:6]
    handoff = (HandoffCode.objects
               .filter(order=order, stage=stage)
               .order_by('-created_at').first())

    if handoff is None:
        return 'not_found', None, 0

    if handoff.used_at:
        # Idempotent: already verified, return ok
        return 'ok', handoff, 0

    if handoff.locked:
        return 'locked', handoff, 0

    if handoff.is_expired:
        return 'expired', handoff, 0

    if cleaned and cleaned == handoff.code:
        handoff.used_at = timezone.now()
        if used_by_user and getattr(used_by_user, 'is_authenticated', False):
            handoff.used_by = used_by_user
        handoff.save(update_fields=['used_at', 'used_by'])
        _notify_admins(
            order,
            f'✓ {handoff.get_stage_display()} verified for order {order.order_number}.',
            link=f'/panel/orders/{order.id}/',
        )
        # Auto-advance to next stage where appropriate
        try:
            advance_after_verify(order, stage)
        except Exception as e:
            logger.exception('Failed to auto-advance after verify: %s', e)
        return 'ok', handoff, 0

    # Wrong code — increment attempts
    handoff.attempts += 1
    if handoff.attempts >= HandoffCode.MAX_ATTEMPTS:
        handoff.locked = True
        handoff.save(update_fields=['attempts', 'locked'])
        _notify_admins(
            order,
            f'🚨 LOCKED: {handoff.get_stage_display()} for order {order.order_number} '
            f'after {handoff.attempts} wrong attempts. Requires manual unlock.',
            link=f'/panel/orders/{order.id}/',
            high_priority=True,
        )
        return 'locked', handoff, 0

    handoff.save(update_fields=['attempts'])
    _notify_admins(
        order,
        f'⚠ Wrong handoff code at {handoff.get_stage_display()} for order {order.order_number} '
        f'({handoff.attempts}/{HandoffCode.MAX_ATTEMPTS}).',
        link=f'/panel/orders/{order.id}/',
    )
    return 'wrong', handoff, handoff.remaining_attempts


def advance_after_verify(order, just_verified_stage):
    """
    Auto-issue the next stage's code based on the order's fulfillment type
    and the stage that was just verified, AND update order.status so the
    customer's tracking page reflects current progress.

    Delivery flow:  admin_to_keeper → keeper_to_rider → rider_to_customer
    Pickup flow:    admin_to_keeper → keeper_to_customer

    Status transitions:
      admin_to_keeper verified   → status = 'processing'  (fulfillment officer has it)
      keeper_to_rider verified   → status = 'dispatched'  (rider has it)
      keeper_to_customer verified → status = 'delivered'  (pickup complete)
      rider_to_customer verified → status = 'delivered'   (customer received)

    Called automatically after verify_code() succeeds. Safe to call multiple
    times — issue_code invalidates any prior unused code for the same stage.
    """
    fulfillment = order.fulfillment_type or 'pickup'

    # Map verified stage → new order status. Only update if the new status
    # is "further along" than the current one (don't downgrade if admin
    # manually set it to something else like 'shipped' or 'delivered').
    status_for_stage = {
        'admin_to_officer':    'processing',
        'officer_to_rider':    'dispatched',
        'officer_to_customer': 'delivered',
        'rider_to_customer':  'delivered',
    }
    # Rank used to compare progress — higher rank = further along.
    # Custom states (cancelled/confirmed) get high rank to be untouchable.
    status_rank = {
        'pending':    0,
        'processing': 1,
        'shipped':    2,
        'dispatched': 2,
        'delivered':  3,
        'confirmed':  4,
        'cancelled':  99,  # cancelled wins over everything — never overwrite
    }
    new_status = status_for_stage.get(just_verified_stage)
    if new_status:
        cur_rank = status_rank.get(order.status, 0)
        new_rank = status_rank.get(new_status, 0)
        # Only update if we're moving forward, and never overwrite cancelled.
        if new_rank > cur_rank and order.status != 'cancelled':
            order.status = new_status
            order.save(update_fields=['status'])

    if just_verified_stage == 'admin_to_officer':
        # Fulfillment Officer has confirmed receipt. Auto-issue next code.
        if fulfillment == 'pickup':
            # Issue code for customer-collection at the branch
            issue_code(
                order,
                'officer_to_customer',
                issued_to_label=f'Customer: {order.full_name or "Customer"}',
            )
        else:
            # Delivery — issue rider code IF rider has already been assigned.
            # If no rider yet, the officer_to_rider code is issued automatically
            # when the fulfillment officer assigns a rider via the assign_rider
            # action (handled in fulfillment_officer_views.py). This is correct
            # behaviour: we can't issue a rider code before we know who the rider is.
            rider = getattr(order, 'rider_delivery', None)
            if rider and rider.rider_phone:
                issue_code(
                    order,
                    'officer_to_rider',
                    issued_to_label=f'Rider: {rider.rider_name}',
                )

    elif just_verified_stage == 'officer_to_rider':
        # Rider has the package. Issue customer-delivery code.
        issue_code(
            order,
            'rider_to_customer',
            issued_to_label=f'Customer: {order.full_name or "Customer"}',
        )

    # ── Delivery-complete notifications ─────────────────────────────────
    # When the customer enters the final code (rider_to_customer for
    # delivery, keeper_to_customer for pickup), notify both the fulfillment officer
    # who handled it AND all admin staff so they know the order is closed.
    if just_verified_stage in ('rider_to_customer', 'officer_to_customer'):
        try:
            _notify_delivery_complete(order, just_verified_stage)
        except Exception as e:
            # Notifications must never break the chain — log and continue
            logger.warning('Delivery-complete notification failed: %s', e)

    # keeper_to_customer and rider_to_customer are terminal — no further codes


def _notify_delivery_complete(order, stage):
    """
    Send "delivery confirmed" notifications to admin + fulfillment officer after
    the customer enters the final handoff code.

    Called automatically from advance_after_verify(). Idempotent — calling
    multiple times for the same order won't spam (each call creates one
    notification per recipient, but the customer can only verify once).
    """
    from .models import Notification
    from django.contrib.auth.models import User as _User

    flow_label = 'pickup' if stage == 'officer_to_customer' else 'delivery'
    title      = f'Order {order.order_number} — {flow_label} confirmed'
    message    = (
        f'Customer {order.full_name or "—"} has confirmed receipt of order '
        f'{order.order_number}. The {flow_label} chain is now complete.'
    )
    link = f'/panel/orders/{order.id}/'

    # Notify all active admin staff via the unified dispatcher
    from . import notify as _notify_svc
    _notify_svc.notify_admins(
        notif_type='delivery_done',
        title=title,
        message=message,
        link=link,
    )

    # Notify the branch fulfillment officer (the one who started the chain)
    if order.branch and order.branch.fulfillment_officer and order.branch.fulfillment_officer.is_active:
        _notify_svc.notify(
            order.branch.fulfillment_officer,
            notif_type='delivery_done',
            title=title,
            message=(
                f'Customer confirmed receipt of order {order.order_number}. '
                f'Chain of custody is closed.'
            ),
            link=f'/officer/order/{order.id}/',
        )


def qr_svg_for_code(code):
    """
    Return an inline SVG string encoding `code` as a QR. Uses the qrcode
    library (already in requirements for 2FA). The SVG is small (~1KB) and
    can be inserted directly into templates with {{ qr_svg|safe }}.
    """
    try:
        import qrcode
        import qrcode.image.svg as svg_factory
    except ImportError:
        logger.warning('qrcode library not installed — QR rendering disabled')
        return ''
    factory = svg_factory.SvgPathImage
    img = qrcode.make(str(code), image_factory=factory, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode('utf-8')


# ─── Internals ────────────────────────────────────────────────────────────────

def _generate_numeric_code():
    """Cryptographically random 6-digit code (000000–999999)."""
    return f'{secrets.randbelow(1_000_000):06d}'


def _send_code_to_recipient(order, handoff, override=None):
    """
    Dispatch the handoff code to the receiving party across all channels:
        - In-app Notification (when recipient is a logged-in User)
        - WhatsApp Cloud API (always tried)
        - SMS (always tried)

    The recipient varies by stage:
        admin_to_officer    → fulfillment officer (logged-in User)
        officer_to_rider    → rider (raw phone, no User account)
        rider_to_customer   → customer (User if registered; phone always)
        officer_to_customer → customer (User if registered; phone always)

    For customer-facing stages the customer HOLDS the code (shows it to
    the rider/officer), so the message text uses "SHOW this" wording.
    For staff-facing stages the receiver ENTERS the code on their portal,
    so the wording is "ENTER this on your portal".
    """
    from . import notify as _notify_svc
    stage = handoff.stage

    # Build a stage-appropriate message. Direction matters: the holder
    # is asked to show/quote it; the receiver is asked to enter it.
    if stage == 'rider_to_customer':
        msg = (
            f'Honey Cave Market — Order {order.order_number}: your delivery code is '
            f'{handoff.code}. SHOW this to the rider when they arrive (read it out '
            f'or hold up the QR on your phone). Valid for {HandoffCode.EXPIRY_MINUTES} '
            f'minutes. Never share this code over the phone.'
        )
        in_app_title = f'🛵 Delivery code ready — Order {order.order_number}'
        in_app_body = (
            'Your rider is on the way. Open this page to see your '
            f'6-digit delivery code ({handoff.code}) — show it to the rider when they arrive.'
        )
        in_app_link = f'/orders/{order.id}/confirm-delivery/'
    elif stage == 'officer_to_customer':
        msg = (
            f'Honey Cave Market — Order {order.order_number}: your pickup code is '
            f'{handoff.code}. SHOW this to the fulfillment officer at the branch when '
            f'collecting your order. Valid for {HandoffCode.EXPIRY_MINUTES} minutes. '
            f'Never share this code over the phone.'
        )
        in_app_title = f'🏪 Pickup code ready — Order {order.order_number}'
        in_app_body = (
            f'Your order is ready at {order.branch.name if order.branch else "the branch"}. '
            f'Open this page to see your pickup code ({handoff.code}) — show it to the officer.'
        )
        in_app_link = f'/orders/{order.id}/confirm-delivery/'
    elif stage == 'admin_to_officer':
        msg = (
            f'Honey Cave Market — Order {order.order_number}: handoff code '
            f'{handoff.code}. ENTER this on your portal to confirm receipt of '
            f'this order. Valid for {HandoffCode.EXPIRY_MINUTES} minutes.'
        )
        in_app_title = f'📦 New handoff code — Order {order.order_number}'
        in_app_body = (
            f'Admin has issued a 6-digit code ({handoff.code}) for order '
            f'{order.order_number}. Enter it on the order page to confirm receipt '
            f'and start fulfillment. Code expires in {HandoffCode.EXPIRY_MINUTES} minutes.'
        )
        in_app_link = f'/officer/order/{order.id}/'
    else:  # officer_to_rider — rider has no in-app account
        msg = (
            f'Honey Cave Market — Order {order.order_number}: handoff code '
            f'{handoff.code}. ENTER this on your portal to confirm receipt. '
            f'Valid for {HandoffCode.EXPIRY_MINUTES} minutes. Don\'t share it.'
        )
        in_app_title = None
        in_app_body = None
        in_app_link = None

    # Resolve recipient — User (for in-app) and/or phone (for WA/SMS)
    recipient_user = None
    phone = ''

    if override:
        phone = (override.get('whatsapp_phone') or override.get('sms_phone') or '').strip()
    else:
        if stage == 'admin_to_officer':
            recipient_user = _get_fulfillment_officer_for(order)
            if recipient_user:
                profile = getattr(recipient_user, 'profile', None)
                phone = (profile.phone if profile else '') or ''
        elif stage == 'officer_to_rider':
            rider = getattr(order, 'rider_delivery', None)
            if rider:
                phone = rider.rider_phone or ''
                # If we have a Rider object backing this delivery, we still
                # don't push in-app because riders don't have user accounts
                # in this system — the magic-link portal is their interface.
        elif stage in ('rider_to_customer', 'officer_to_customer'):
            recipient_user = order.user if order.user_id else None
            phone = (order.phone or '').strip()

    # ── Channel dispatch ────────────────────────────────────────────
    # If we have a User: notify() handles in-app + WA + SMS in one call.
    # If we only have a phone: notify_phone() handles WA + SMS.
    # The 'handoff_code' WhatsApp template is preferred when configured —
    # plain text falls back outside the 24-hour window.

    if recipient_user and in_app_title:
        _notify_svc.notify(
            recipient_user,
            notif_type='order_update',
            title=in_app_title,
            message=in_app_body,
            link=in_app_link,
            whatsapp_template='handoff_code',
            whatsapp_template_vars=[handoff.code],
            sms_text=msg,
            phone_override=phone,
        )
    elif phone:
        # No User account on the recipient (rider, or guest customer).
        _notify_svc.notify_phone(
            phone,
            whatsapp_template='handoff_code',
            whatsapp_template_vars=[handoff.code],
            sms_text=msg,
        )


def _get_fulfillment_officer_for(order):
    """Return the User assigned as fulfillment officer for the order's branch, or None."""
    branch = getattr(order, 'branch', None)
    if branch is None:
        return None
    return getattr(branch, 'fulfillment_officer', None)


def _notify_admins(order, message, link='', high_priority=False):
    """Send an in-app notification to every active staff user."""
    title = 'Handoff event'
    if high_priority:
        title = '🚨 Handoff alert'
    try:
        from . import notify as _notify_svc
        _notify_svc.notify_admins(
            notif_type='stock_alert' if high_priority else 'order_update',
            title=title,
            message=message,
            link=link or f'/panel/orders/{order.id}/',
        )
    except Exception as e:
        logger.exception('Failed to notify admins: %s', e)
