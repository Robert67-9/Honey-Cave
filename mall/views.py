import json
import logging
import math
import urllib.request
import urllib.error
import urllib.parse
from decimal import Decimal
from django.db.models import F
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponseBadRequest
from django.core.mail import send_mail, EmailMultiAlternatives
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings as django_settings
from .models import (
    Product, Category, Order, OrderItem, Branch, BranchProduct,
    AuditLog, StockReservation, Promotion,
    REGION_FEES, REGION_CHOICES, OTPVerification, UserProfile, PaymentSettings,
    calculate_delivery_fee, DELIVERY_BASE_FEE,
    Review, ReviewHelpful, WishlistItem, PromoCode, ProductImage, OrderNote, Notification,
    OrderFeedback, RiderDelivery,
)
from .forms import (
    RegisterForm, CheckoutForm, ReviewForm,
    OTPVerifyForm, ForgotPasswordForm, ResetPasswordForm,
    ContactForm, ProfileUpdateForm, PromoCodeForm, OrderFeedbackForm,
)
from .security import (
    rate_limit, check_rate_limit, clear_rate_limit,
    is_valid_otp, constant_time_compare,
    sanitize_text, safe_redirect_url,
    check_otp_rate_limit, clear_otp_rate_limit,
    record_otp_failure, is_otp_locked_out, clear_otp_failures,
)


logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_cart(request):
    return request.session.get('cart', {})

def save_cart(request, cart):
    request.session['cart'] = cart
    request.session.modified = True

def get_cart_details(request):
    """
    Build display cart from session data using fresh stock values from DB.
    Quantities are capped against current stock so the UI stays accurate,
    but a final atomic stock check is still required at checkout submission
    (see BUG-04 fix in the checkout view).
    """
    cart = get_cart(request)
    if not cart:
        return [], Decimal('0')

    # BUG-01 FIX: bulk-fetch all cart products in one query so stock values
    # are fresh at render time, not stale from a previous page load.
    try:
        product_ids = [int(pid) for pid in cart.keys()]
    except (ValueError, TypeError):
        return [], Decimal('0')

    products_by_id = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids, available=True)
    }

    cart_items, subtotal = [], Decimal('0')
    for product_id, qty in cart.items():
        try:
            product = products_by_id.get(int(product_id))
            if product is None:
                continue
            qty = max(1, min(int(qty), product.stock, 99))
            item_total = product.price * qty
            subtotal += item_total
            cart_items.append({'product': product, 'quantity': qty, 'total': item_total})
        except (ValueError, OverflowError):
            pass
    return cart_items, subtotal


# ─── Email helpers ────────────────────────────────────────────────────────────

def send_otp(user, otp_obj, *, plaintext_code):
    """
    Deliver an OTP across all channels available for this user:
        - Email (always — even when other channels work, email is a useful audit trail)
        - WhatsApp (when user.profile.phone is set)
        - SMS (same condition)

    `plaintext_code` is the unhashed 6-digit code. Callers must pass it
    in explicitly because the OTP row itself stores only a hash going
    forward (Sprint 2). Never rebuild the code from the row — that would
    require storing the plaintext, which we deliberately don't.

    All channel failures are logged but never raised. If literally nothing
    delivers, the caller still gets a successful create; admin can manually
    inspect and re-send via /resend-otp/.

    Returns dict {email: bool, whatsapp: bool, sms: bool} for diagnostics.
    """
    result = {'email': False, 'whatsapp': False, 'sms': False}

    purpose_label = 'Sign Up Verification' if otp_obj.purpose == 'signup' else 'Password Reset'
    subject = f'Honey Cave Market — {purpose_label} Code'
    plain_msg = (
        f'Hi {user.first_name or user.username},\n\n'
        f'Your Honey Cave Market verification code is:\n\n'
        f'  {plaintext_code}\n\n'
        f'This code expires in 10 minutes. Do not share it with anyone.\n\n'
        f'If you did not request this, please ignore this email.\n\n'
        f'— Honey Cave Market'
    )

    # ── Email ─────────────────────────────────────────────────────────
    try:
        send_mail(
            subject, plain_msg,
            django_settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=True,
        )
        result['email'] = True
    except Exception as e:
        logger.warning('OTP email send failed for user %s: %s', user.pk, e)

    # ── WhatsApp + SMS ────────────────────────────────────────────────
    profile = getattr(user, 'profile', None)
    phone = (profile.phone if profile else '') or ''
    if phone:
        # Short message tuned for SMS (160-char ceiling per segment).
        sms_text = (
            f'Honey Cave: Your {purpose_label.lower()} code is {plaintext_code}. '
            f'Expires in 10 min. Do not share.'
        )
        wa_text = (
            f'*Honey Cave Market*\n\n'
            f'Your {purpose_label.lower()} code is *{plaintext_code}*.\n\n'
            f'Valid for 10 minutes. Never share this code.'
        )
        try:
            from .notify import notify_phone
            r = notify_phone(phone=phone, whatsapp_text=wa_text, sms_text=sms_text)
            result['whatsapp'] = r.get('whatsapp', False)
            result['sms']      = r.get('sms', False)
        except Exception as e:
            logger.warning('OTP phone send failed for user %s: %s', user.pk, e)

    # Console/terminal fallback for local testing — prints the code to the
    # server console when OTP_CONSOLE_FALLBACK is on (defaults to DEBUG).
    # This guarantees the code is retrievable even with no SMS/WhatsApp set up.
    try:
        from . import sms as _sms
        if _sms.console_otp(phone or user.email or user.username,
                            plaintext_code, label=f'{purpose_label} code'):
            result['console'] = True
    except Exception:
        pass

    return result


# Backward-compat shim: legacy callers used send_otp_email(user, otp_obj).
# The new helper needs the plaintext code (since the OTP row hashes it).
# Old callers pass a row that still has .code as plaintext (DB rows
# created before Sprint-2 migration); new callers pass plaintext_code
# explicitly. We accept both shapes here so nothing breaks during rollout.
def send_otp_email(user, otp_obj):
    """Deprecated. Kept for any imports we missed; routes to send_otp."""
    plaintext = getattr(otp_obj, '_plaintext_code', None) or getattr(otp_obj, 'code', '')
    return send_otp(user, otp_obj, plaintext_code=plaintext)


def send_order_receipt(order):
    """Send HTML + plain-text receipt email after a successful order."""
    customer_name = order.full_name or order.user.get_full_name() or order.user.username
    items_html = ''
    items_text = ''
    for item in order.items.select_related('product').all():
        total = item.get_total_price()
        items_html += f'''
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #f0ebe2;font-size:14px;color:#1A1410;">{item.product.name}</td>
          <td style="padding:10px 0;border-bottom:1px solid #f0ebe2;font-size:14px;color:#7A6E64;text-align:center;">× {item.quantity}</td>
          <td style="padding:10px 0;border-bottom:1px solid #f0ebe2;font-size:14px;color:#1A1410;text-align:right;font-weight:600;">GH₵ {total}</td>
        </tr>'''
        items_text += f'  {item.product.name} x{item.quantity}  —  GH₵ {total}\n'

    delivery_fee_line = f'GH₵ {order.shipping_fee}' if order.shipping_fee else 'FREE'
    if order.fulfillment_type == 'delivery':
        fulfillment_html = f'''<tr><td colspan="3" style="padding:16px 0 4px;">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#C9A84C;">🛵 Home Delivery</p>
          <p style="margin:0;font-size:13px;color:#1A1410;">{order.delivery_address or 'Address provided at checkout'}</p>
          <p style="margin:4px 0 0;font-size:12px;color:#7A6E64;">Delivery fee: {delivery_fee_line}</p>
          <p style="margin:4px 0 0;font-size:12px;color:#7A6E64;">Estimated: within 24 hours. We'll call before arrival.</p>
        </td></tr>'''
        fulfillment_text = f'Delivery to: {order.delivery_address or "Address on file"}\nDelivery fee: {delivery_fee_line}\nEstimated: within 24 hours\n'
    elif order.branch:
        b = order.branch
        maps_link = f'https://www.google.com/maps/search/?api=1&query={b.latitude},{b.longitude}' if b.latitude and b.longitude else ''
        fulfillment_html = f'''<tr><td colspan="3" style="padding:16px 0 4px;">
          <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#C9A84C;">🏢 Branch Pickup</p>
          <p style="margin:0;font-size:14px;font-weight:700;color:#1A1410;">{b.name}</p>
          <p style="margin:3px 0 0;font-size:13px;color:#7A6E64;">{b.address}, {b.city}</p>
          {'<p style="margin:3px 0 0;font-size:12px;color:#7A6E64;">📞 ' + b.phone + '</p>' if b.phone else ''}
          {'<p style="margin:3px 0 0;font-size:12px;color:#7A6E64;">🕐 ' + b.opening_hours + '</p>' if b.opening_hours else ''}
          {'<p style="margin:4px 0 0;"><a href="' + maps_link + '" style="color:#C9A84C;font-size:12px;">Open in Google Maps</a></p>' if maps_link else ''}
        </td></tr>'''
        fulfillment_text = f'Pickup: {b.name}\n{b.address}, {b.city}\n'
    else:
        fulfillment_html = fulfillment_text = ''

    subject = f'Honey Cave Market — Order {order.order_number} Confirmed ✅'
    plain_text = (
        f'Hi {customer_name},\n\nThank you for shopping at Market!\n\n'
        f'Order: {order.order_number}\nDate: {order.created.strftime("%d %b %Y, %I:%M %p")}\n'
        f'Status: {order.get_status_display()}\n\n'
        f'ITEMS\n{"─"*32}\n{items_text}\n'
        f'Subtotal:  GH₵ {order.subtotal()}\nShipping:  GH₵ {order.shipping_fee}\nTOTAL:     GH₵ {order.total_price}\n\n'
        f'{fulfillment_text}\n— Market Team'
    )
    html_message = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#FAF7F2;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FAF7F2;padding:32px 16px;">
<tr><td align="center"><table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
<tr><td style="background:#1A1410;border-radius:14px 14px 0 0;padding:28px 36px;text-align:center;">
  <p style="margin:0;font-size:24px;font-weight:700;color:#C9A84C;letter-spacing:2px;font-family:Georgia,serif;">MARKET</p>
  <p style="margin:6px 0 0;font-size:11px;color:rgba(255,255,255,0.45);letter-spacing:2px;text-transform:uppercase;">Order Confirmation</p>
</td></tr>
<tr><td style="background:#27AE60;padding:14px 36px;text-align:center;">
  <p style="margin:0;color:#fff;font-size:15px;font-weight:600;">✅ &nbsp; Order {order.order_number} confirmed — payment received</p>
</td></tr>
<tr><td style="background:#ffffff;padding:36px 36px 28px;border:1px solid #E8E0D4;border-top:none;">
  <p style="margin:0 0 24px;font-size:16px;color:#1A1410;">Hi <strong>{customer_name}</strong>,</p>
  <p style="margin:0 0 28px;font-size:14px;color:#5a5047;line-height:1.7;">Thank you for your order! Your payment has been received and confirmed. We're now preparing your order.</p>
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#FAF7F2;border-radius:10px;padding:18px 20px;margin-bottom:28px;">
    <tr>
      <td style="font-size:12px;color:#7A6E64;">Order Number</td>
      <td style="font-size:12px;color:#7A6E64;">Date</td>
    </tr>
    <tr>
      <td style="font-size:15px;font-weight:700;color:#1A1410;padding-top:5px;">{order.order_number}</td>
      <td style="font-size:14px;font-weight:600;color:#1A1410;padding-top:5px;">{order.created.strftime('%d %b %Y')}</td>
    </tr>
  </table>
  <p style="margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#7A6E64;">Items Ordered</p>
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <th style="text-align:left;font-size:11px;color:#7A6E64;font-weight:600;padding-bottom:8px;">Product</th>
      <th style="text-align:center;font-size:11px;color:#7A6E64;font-weight:600;padding-bottom:8px;">Qty</th>
      <th style="text-align:right;font-size:11px;color:#7A6E64;font-weight:600;padding-bottom:8px;">Price</th>
    </tr>
    {items_html}
    {fulfillment_html}
    <tr><td colspan="2" style="padding-top:14px;font-size:13px;color:#7A6E64;">Subtotal</td><td style="padding-top:14px;font-size:13px;color:#7A6E64;text-align:right;">GH₵ {order.subtotal()}</td></tr>
    <tr><td colspan="2" style="padding-top:4px;font-size:13px;color:#1A1410;">Shipping</td><td style="padding-top:4px;font-size:13px;color:#1A1410;text-align:right;">GH₵ {order.shipping_fee}</td></tr>
    <tr><td colspan="2" style="padding-top:12px;border-top:2px solid #1A1410;font-size:16px;font-weight:700;color:#1A1410;">Total Paid</td>
        <td style="padding-top:12px;border-top:2px solid #1A1410;font-size:18px;font-weight:700;color:#C9A84C;text-align:right;">GH₵ {order.total_price}</td></tr>
  </table>
</td></tr>
<tr><td style="background:#1A1410;border-radius:0 0 14px 14px;padding:22px 36px;text-align:center;">
  <p style="margin:0 0 6px;font-size:12px;color:rgba(255,255,255,0.5);">Questions? <a href="mailto:{django_settings.SUPPORT_EMAIL}" style="color:#C9A84C;text-decoration:none;">{django_settings.SUPPORT_EMAIL}</a></p>
  <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.3);">© Market · Ghana</p>
</td></tr>
</table></td></tr></table></body></html>'''

    try:
        msg = EmailMultiAlternatives(
            subject=subject, body=plain_text,
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            to=[order.email],
        )
        msg.attach_alternative(html_message, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass



# ─── Notification Helpers ────────────────────────────────────────────────────

def _notify(user, notif_type, title, message, link=''):
    """Create an in-app Notification for a user."""
    Notification.objects.create(
        user=user, notif_type=notif_type,
        title=title, message=message, link=link,
    )


def _notify_admins_new_order(order):
    """Notify all active staff (in-app + WhatsApp + SMS) when a new order is placed."""
    from .notify import notify_admins
    phone_msg = (
        f'Honey Cave: New order {order.order_number} — GH₵ {order.total_price} '
        f'from {order.full_name}. {order.get_fulfillment_type_display()}.'
    )
    notify_admins(
        notif_type='new_order',
        title=f'New Order {order.order_number} — GH₵ {order.total_price}',
        message=f'{order.full_name} placed an order for GH₵ {order.total_price}.',
        link=f'/panel/orders/{order.id}/',
        whatsapp_text=phone_msg,
        sms_text=phone_msg,
    )


def _autostart_handoff_chain(order):
    """
    Auto-issue the admin_to_officer code right after payment so the
    fulfillment officer can start working on the order even if no admin
    is online. Without this, every paid order would sit waiting for an
    admin to manually click "Issue Code", which is operationally fragile
    (nights, weekends, sick days).

    Officer resolution order (first match wins):
      1. Branch.fulfillment_officer (legacy primary FK) — must be active.
      2. BranchAssignment with role='primary', status='approved' — active.
      3. ANY BranchAssignment with status='approved' — active. ← NEW.
         Catches cases where there's no primary on file but a secondary
         officer is approved for this branch (e.g. holiday cover, where
         the primary's BranchAssignment was revoked but secondaries
         remain).

    Behaviour matrix:
      - Branch + ANY active officer  → issue the code, notify officer via
        in-app + WhatsApp + SMS.
      - Branch but no active officer → log + alert all admins in-app so
        they can either assign an officer or manually issue the code.
      - No branch on the order       → silent skip (shouldn't happen
        post-payment, but we don't blow up).

    Never raises — chain auto-start is best-effort.

    Returns a (success: bool, reason: str) tuple — the admin order
    detail page uses `reason` to display "Why no code yet?" instead of
    a vague "No code issued yet" message.
    """
    from . import handoff as _handoff_svc
    from .models import BranchAssignment

    if not order.branch:
        return False, 'Order has no branch — cannot resolve a fulfillment officer.'

    branch = order.branch

    # 1. Legacy primary FK
    officer = branch.fulfillment_officer if (
        branch.fulfillment_officer and branch.fulfillment_officer.is_active
    ) else None

    # 2. Approved primary via BranchAssignment
    if officer is None:
        primary = (BranchAssignment.objects
                   .filter(branch=branch, role='primary',
                           status='approved', officer__is_active=True)
                   .select_related('officer').first())
        if primary:
            officer = primary.officer

    # 3. Any approved secondary (NEW fallback)
    if officer is None:
        secondary = (BranchAssignment.objects
                     .filter(branch=branch, status='approved',
                             officer__is_active=True)
                     .select_related('officer').first())
        if secondary:
            officer = secondary.officer

    if officer is None:
        reason = (
            f'No active fulfillment officer assigned to {branch.name}. '
            f'Assign one in Panel → Fulfillment Officers, then click "Issue Code".'
        )
        logger.warning(
            'Handoff auto-start blocked: order %s — %s',
            order.order_number, reason,
        )
        try:
            from .notify import notify_admins
            notify_admins(
                notif_type='order_update',
                title=f'⚠️ No officer at {branch.name}',
                message=(
                    f'Order {order.order_number} is paid but {branch.name} '
                    f'has no active fulfillment officer. Assign one '
                    f'(or issue the handoff code manually) to start fulfilment.'
                ),
                link=f'/panel/orders/{order.id}/',
            )
        except Exception as e:
            logger.warning('No-officer alert failed for %s: %s', order.order_number, e)
        return False, reason

    try:
        _handoff_svc.issue_code(
            order, 'admin_to_officer',
            issued_to_label=f'Fulfillment Officer: {officer.username}',
        )
        logger.info(
            'Auto-issued handoff code for order %s to officer %s at %s',
            order.order_number, officer.username, branch.name,
        )
        return True, f'Auto-issued to {officer.get_full_name() or officer.username}.'
    except Exception as e:
        reason = f'Could not issue code automatically: {e}'
        logger.warning(
            'Auto-start of handoff chain failed for order %s: %s',
            order.order_number, e,
        )
        return False, reason


def _diagnose_handoff_state(order):
    """
    Compute a friendly explanation of WHY a paid order doesn't yet have
    an active admin_to_officer code. Used by the admin order-detail
    template to replace the unhelpful "No code issued yet" message with
    something actionable.

    Returns dict {can_autostart: bool, reason: str, officer_name: str}.
    """
    from .models import BranchAssignment

    if not order.branch:
        return {'can_autostart': False, 'officer_name': '',
                'reason': 'Order has no branch on file.'}

    branch = order.branch
    candidate = None
    source = ''

    if branch.fulfillment_officer and branch.fulfillment_officer.is_active:
        candidate = branch.fulfillment_officer
        source = 'primary on Branch record'
    else:
        primary = (BranchAssignment.objects
                   .filter(branch=branch, role='primary',
                           status='approved', officer__is_active=True)
                   .select_related('officer').first())
        if primary:
            candidate = primary.officer
            source = 'primary BranchAssignment'
        else:
            secondary = (BranchAssignment.objects
                         .filter(branch=branch, status='approved',
                                 officer__is_active=True)
                         .select_related('officer').first())
            if secondary:
                candidate = secondary.officer
                source = 'secondary BranchAssignment'

    if candidate is None:
        return {
            'can_autostart': False,
            'officer_name': '',
            'reason': (
                f'{branch.name} has no active fulfillment officer. '
                f'Assign one under Panel → Fulfillment Officers first.'
            ),
        }

    name = candidate.get_full_name() or candidate.username
    return {
        'can_autostart': True,
        'officer_name': name,
        'reason': f'Will issue to {name} ({source}).',
    }


def _notify_customer_status_change(order):
    """
    Notify the customer when their order status changes.

    Uses the unified notify() dispatcher: in-app + WhatsApp + SMS together.
    Skips WA/SMS for statuses where text-blasting the customer's phone
    isn't valuable (e.g. internal status transitions).
    """
    from .notify import notify, notify_phone

    status_msgs = {
        'processing': 'Your payment has been verified and your order is being prepared.',
        'shipped':    'Great news — your order is on its way!',
        'delivered':  'Your order has been delivered. Enjoy your purchase!',
        'cancelled':  'Your order has been cancelled. Contact us if you have questions.',
    }
    msg = status_msgs.get(
        order.status,
        f'Your order status has been updated to: {order.get_status_display()}.',
    )

    # WA/SMS only for the milestones the customer cares about — not every
    # internal status flicker.
    notify_phone_too = order.status in ('processing', 'shipped', 'delivered', 'cancelled')
    sms_text = None
    wa_text = None
    if notify_phone_too:
        wa_text = (
            f'*Honey Cave Market* — Order {order.order_number}\n\n'
            f'{msg}\n\n'
            f'Track your order: {django_settings.SITE_URL}/my-orders/'
        )
        # SMS is shorter — strip the markdown asterisks
        sms_text = (
            f'Honey Cave: Order {order.order_number} — {msg} '
            f'Track: {django_settings.SITE_URL}/my-orders/'
        )

    # Resolve phone — prefer the order's phone over profile.phone
    phone = ''
    if order.user_id:
        prof = getattr(order.user, 'profile', None)
        phone = (prof.phone if prof else '') or ''
    phone = phone or order.phone or ''

    if order.user_id:
        notify(
            order.user,
            notif_type='order_update',
            title=f'Order {order.order_number}: {order.get_status_display()}',
            message=msg,
            link='/my-orders/',
            whatsapp_text=wa_text,
            sms_text=sms_text,
            phone_override=phone,
        )
    elif phone and (wa_text or sms_text):
        # Guest checkout — no User account but they still want phone updates
        notify_phone(phone=phone, whatsapp_text=wa_text, sms_text=sms_text)

    # When delivered: also send feedback request email
    if order.status == 'delivered':
        _send_feedback_request_email(order)


def _send_feedback_request_email(order):
    """Send a polite email inviting the customer to leave feedback after delivery."""
    try:
        feedback_url = f'/orders/{order.id}/feedback/'
        subject = f'How was your Market order {order.order_number}?'
        plain = (
            f'Hi {order.full_name},\n\n'
            f'Your order {order.order_number} has been delivered. We hope you love your purchase!\n\n'
            f'It would mean a lot if you shared your experience:\n'
            f'{feedback_url}\n\n'
            f'It only takes 1 minute. Thank you!\n\n— The Market Team'
        )
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:32px;background:#fff;">
          <h2 style="color:#C9A84C;">How was your order?</h2>
          <p>Hi <strong>{order.full_name}</strong>,</p>
          <p>Your order <strong>{order.order_number}</strong> has been delivered 🎉</p>
          <p>We'd love to hear how it went — your feedback helps us serve you better.</p>
          <a href="{feedback_url}" style="display:inline-block;margin:16px 0;padding:12px 28px;background:#C9A84C;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;">
            Leave Feedback →
          </a>
          <p style="color:#888;font-size:13px;">Thank you for shopping with Market!</p>
        </div>"""
        from django.core.mail import EmailMultiAlternatives
        msg = EmailMultiAlternatives(subject, plain, django_settings.DEFAULT_FROM_EMAIL, [order.email])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass


def _check_stock_alerts(product):
    """FEAT-04: Notify admin staff when a product's stock falls below threshold."""
    LOW_STOCK_THRESHOLD = getattr(django_settings, 'LOW_STOCK_THRESHOLD', 5)
    product.refresh_from_db(fields=['stock'])
    if product.stock <= LOW_STOCK_THRESHOLD:
        from django.contrib.auth.models import User as _User
        staff_users = _User.objects.filter(is_staff=True, is_active=True)
        for staff in staff_users:
            _notify(
                user=staff,
                notif_type='stock_alert',
                title=f'Low Stock: {product.name} ({product.stock} left)',
                message=(
                    f'"{product.name}" has only {product.stock} unit(s) remaining. '
                    f'Consider restocking soon.'
                ),
                link=f'/panel/products/{product.pk}/edit/',
            )


# ─── Home ────────────────────────────────────────────────────────────────────

def home(request):
    categories = Category.objects.all()
    featured   = (Product.objects.filter(available=True)
                  .select_related('category')
                  .prefetch_related('gallery')[:8])
    wishlist_ids = set()
    if request.user.is_authenticated:
        wishlist_ids = set(
            WishlistItem.objects.filter(user=request.user)
            .values_list('product_id', flat=True)
        )
    return render(request, 'mall/home.html', {'categories': categories, 'featured': featured, 'wishlist_ids': wishlist_ids})


# ─── Products ────────────────────────────────────────────────────────────────

def product_list(request):
    from django.db.models import Q
    import hashlib, math

    category_slug    = request.GET.get('category')
    query            = request.GET.get('q', '')[:200]
    sort             = request.GET.get('sort', '')  # price_asc, price_desc, newest, name
    products         = Product.objects.filter(available=True).select_related('category').prefetch_related('gallery')
    categories       = Category.objects.all()
    current_category = None

    if category_slug:
        current_category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=current_category)
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(category__name__icontains=query)
        )

    # Explicit sort overrides
    if sort in ('price_asc', 'price_desc', 'newest', 'name'):
        if sort == 'price_asc':
            products = products.order_by('price', 'id')
        elif sort == 'price_desc':
            products = products.order_by('-price', 'id')
        elif sort == 'newest':
            products = products.order_by('-created', 'id')
        elif sort == 'name':
            products = products.order_by('name')
        paginator = Paginator(products, 50)
        page_obj  = paginator.get_page(request.GET.get('page'))
        wishlist_ids = set()
        if request.user.is_authenticated:
            wishlist_ids = set(
                WishlistItem.objects.filter(user=request.user)
                .values_list('product_id', flat=True)
            )
        return render(request, 'mall/product_list.html', {
            'products': page_obj, 'page_obj': page_obj,
            'categories': categories,
            'current_category': current_category, 'query': query,
            'wishlist_ids': wishlist_ids,
            'sort': sort,
        })
    else:
        # Default: SEO-friendly deterministic shuffle.
        # Products are bucketed by stock/featured tier then shuffled
        # using a daily-rotating seed so crawlers see stable URLs
        # but customers see variety.  Uses DB id % bucket to avoid
        # a full in-memory sort on large catalogues.
        # Deterministic daily shuffle — pure Python, works on all DBs/backends
        from datetime import date
        seed = int(hashlib.md5(str(date.today()).encode()).hexdigest(), 16)
        items = list(products.order_by('-stock', 'id'))
        import random
        rng = random.Random(seed)
        # Split into tiers so well-stocked items stay near top but shuffle within tier
        in_stock  = [p for p in items if p.stock > 0]
        out_stock = [p for p in items if p.stock == 0]
        rng.shuffle(in_stock)
        rng.shuffle(out_stock)
        from django.core.paginator import Paginator as _Pag
        all_items = in_stock + out_stock
        paginator = _Pag(all_items, 50)
        page_obj  = paginator.get_page(request.GET.get('page'))
        wishlist_ids = set()
        if request.user.is_authenticated:
            wishlist_ids = set(
                WishlistItem.objects.filter(user=request.user)
                .values_list('product_id', flat=True)
            )
        return render(request, 'mall/product_list.html', {
            'products': page_obj, 'page_obj': page_obj,
            'categories': categories,
            'current_category': current_category, 'query': query,
            'wishlist_ids': wishlist_ids,
            'sort': sort,
        })



def product_detail(request, slug):
    from django.db.models import Avg, Count, Q as DQ

    # PERF-02 FIX: annotate avg_rating and review_count directly on the queryset
    # so they resolve in a single SQL query rather than 3 separate DB round-trips
    # per product page load.
    product = get_object_or_404(
        Product.objects.annotate(
            annotated_avg=Avg('reviews__rating',  filter=DQ(reviews__is_approved=True)),
            annotated_count=Count('reviews',       filter=DQ(reviews__is_approved=True)),
        ),
        slug=slug, available=True,
    )
    reviews     = product.reviews.filter(is_approved=True).select_related('user')
    review_form = ReviewForm()

    # Has this user already reviewed?
    user_review = None
    has_purchased = False
    pending_order_item = None   # the specific item to link the review to

    if request.user.is_authenticated:
        try:
            user_review = product.reviews.get(user=request.user)
        except Review.DoesNotExist:
            pass

        # Check for a delivered order containing this product
        delivered_item = OrderItem.objects.filter(
            order__user=request.user,
            order__status='delivered',
            product=product,
        ).select_related('order').first()
        if delivered_item:
            has_purchased = True
            # Only link if not already reviewed
            if not user_review:
                pending_order_item = delivered_item

    if request.method == 'POST' and request.user.is_authenticated:
        if not check_rate_limit('review', request, limit=5, window=60):
            messages.error(request, 'Too many submissions. Please slow down.')
            return redirect('product_detail', slug=slug)
        if user_review:
            messages.warning(request, 'You have already reviewed this product.')
            return redirect('product_detail', slug=slug)
        review_form = ReviewForm(request.POST)
        if review_form.is_valid():
            r = review_form.save(commit=False)
            r.product = product
            r.user    = request.user
            r.rating  = int(review_form.cleaned_data['rating'])
            if pending_order_item:
                r.order_item           = pending_order_item
                r.is_verified_purchase = True
            r.save()
            messages.success(request, '✅ Thank you! Your review has been submitted.')
            return redirect('product_detail', slug=slug)

    # Use annotated values — avoids 2 extra DB queries vs calling model methods
    avg   = round(product.annotated_avg, 1) if product.annotated_avg else None
    count = product.annotated_count
    # rating_breakdown still uses model method (needs per-star counts — one query)
    breakdown = product.rating_breakdown()

    return render(request, 'mall/product_detail.html', {
        'product':             product,
        'reviews':             reviews,
        'review_form':         review_form,
        'user_review':         user_review,
        'has_purchased':       has_purchased,
        'avg_rating':          avg,
        'review_count':        count,
        'rating_breakdown':    breakdown,
        'is_wishlisted': WishlistItem.objects.filter(
            user=request.user, product=product
        ).exists() if request.user.is_authenticated else False,
    })


# ─── Cart ─────────────────────────────────────────────────────────────────────

def cart_view(request):
    cart_items, subtotal = get_cart_details(request)
    return render(request, 'mall/cart.html', {'cart_items': cart_items, 'total': subtotal})

@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id, available=True)
    if product.stock < 1:
        messages.warning(request, f'"{product.name}" is out of stock.')
        referer = request.META.get('HTTP_REFERER', '')
        url = safe_redirect_url(referer, request, fallback='/products/')
        # Anchor back to the product card so the browser keeps the user's
        # place on the listing instead of jumping to the top of the page.
        url = url.split('#')[0] + f'#product-{product_id}'
        return redirect(url)
    cart = get_cart(request)
    key  = str(product_id)
    new_qty = min(cart.get(key, 0) + 1, product.stock, 99)
    cart[key] = new_qty
    save_cart(request, cart)
    messages.success(request, f'"{product.name}" added to cart!')
    referer = request.META.get('HTTP_REFERER', '')
    url = safe_redirect_url(referer, request, fallback='/cart/')
    # If the user came from a product listing, keep them on the same card.
    # For product_detail or cart page fallbacks the anchor will be a no-op.
    url = url.split('#')[0] + f'#product-{product_id}'
    return redirect(url)

@require_POST
def remove_from_cart(request, product_id):
    cart = get_cart(request)
    cart.pop(str(product_id), None)
    save_cart(request, cart)
    return redirect('cart')

@require_POST
def update_cart(request, product_id):
    cart = get_cart(request)
    key  = str(product_id)
    try:
        qty = int(request.POST.get('quantity', 1))
    except (ValueError, TypeError):
        return HttpResponseBadRequest('Invalid quantity.')
    qty = max(0, min(qty, 99))
    if qty > 0:
        cart[key] = qty
    else:
        cart.pop(key, None)
    save_cart(request, cart)
    return redirect('cart')


# ─── Branch API ──────────────────────────────────────────────────────────────

def branches_by_region_api(request):
    region = request.GET.get('region', '')
    valid_regions = {k for k, _ in REGION_CHOICES}
    if region not in valid_regions:
        return JsonResponse({'error': 'Invalid region'}, status=400)
    branches = Branch.objects.filter(region=region, is_active=True).values(
        'id', 'name', 'address', 'city', 'phone', 'opening_hours', 'landmark'
    )
    fee = float(REGION_FEES.get(region, Decimal('15.00')))
    return JsonResponse({'branches': list(branches), 'shipping_fee': fee})


def payment_methods_api(request):
    """
    Return all active payment methods that are ALSO backed by a finished
    """
    from .payments.dispatch import GATEWAY_REGISTRY

    # One query, hydrate each instance once for the icon + display methods.
    rows = list(PaymentSettings.objects.filter(is_active=True).order_by('created_at'))
    methods_list = []
    for ps in rows:
        adapter_cls = GATEWAY_REGISTRY.get(ps.provider)
        # Skip unknown providers (registry mismatch) and stub adapters.
        if adapter_cls is None or not getattr(adapter_cls, 'is_ready', False):
            continue
        methods_list.append({
            'id':             ps.id,
            'provider':       ps.provider,
            'display_name':   ps.get_provider_display(),
            'icon':           ps.get_icon(),
            'account_name':   ps.account_name,
            'account_number': ps.account_number,
            'instructions':   ps.instructions,
        })
    return JsonResponse({'payment_methods': methods_list})


# ─── Paystack Verification ────────────────────────────────────────────────────

@require_POST
@login_required
def paystack_verify(request):
    """
    Verify a Paystack transaction server-side before finalising an order.

    Called via AJAX from the checkout page after the Paystack popup closes
    successfully. Delegates the actual gateway call to the Paystack adapter
    in mall/payments/paystack.py so all gateway-specific logic (key
    handling, error parsing, mode-mismatch detection) lives in one place.

    Returns JSON: {verified: true, amount_kobo: int} or {verified: false, error: str}.
    On success the verified reference + amount are anchored in the session
    so the checkout view can confirm the customer paid the right amount.
    """
    from .payments import dispatch as _dispatch

    try:
        body = json.loads(request.body)
        reference = body.get('reference', '').strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'verified': False, 'error': 'Invalid request body.'}, status=400)

    if not reference:
        return JsonResponse({'verified': False, 'error': 'No reference supplied.'}, status=400)

    adapter, _ps = _dispatch.gateway_by_provider('paystack')
    if adapter is None:
        return JsonResponse({
            'verified': False,
            'error': 'Paystack is not configured. Add it under Panel → Payment Methods.',
        }, status=500)

    result = adapter.verify_payment(reference)

    if not result.success:
        # Adapter signalled an error reaching/parsing Paystack — surface it.
        return JsonResponse(
            {'verified': False, 'error': result.error_message or 'Verification failed.'},
            status=502 if 'reach' in (result.error_message or '').lower() else 400,
        )

    if not result.is_paid:
        # Adapter reached Paystack and got a "transaction was not successful" answer.
        return JsonResponse(
            {'verified': False, 'error': result.error_message or 'Transaction not successful.'},
            status=400,
        )

    # Anchor the verified reference + amount in the session so checkout() can
    # confirm the paid amount matches the order total server-side. This is
    # the critical anti-tamper step: an attacker can't pay ₵1, get a verified
    # ref, then submit a ₵500 order — checkout() compares pesewas-for-pesewas.
    request.session['paystack_verified_ref']     = reference
    request.session['paystack_verified_pesewas'] = str(result.amount_pesewas)
    return JsonResponse({'verified': True, 'amount_kobo': result.amount_pesewas})



@require_POST
@login_required
def paystack_init_link(request):
    """Return a short-lived embed link for Paystack checkout.

    Frontend expects JSON: {ok: True, link: str, reference: str} on success.
    """
    from .payments import dispatch as _dispatch
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'Invalid request body.'}, status=400)

    amount_pesewas = int(body.get('amount_pesewas', 0))
    email = (body.get('email') or '').strip()
    phone = (body.get('phone') or '').strip()
    order_ref = (body.get('order_ref') or '').strip()

    if amount_pesewas <= 0:
        return JsonResponse({'ok': False, 'error': 'Invalid amount.'}, status=400)

    adapter, _ps = _dispatch.gateway_by_provider('paystack')
    if adapter is None:
        return JsonResponse({'ok': False, 'error': 'Paystack is not configured.'}, status=500)

    site_url = getattr(django_settings, 'SITE_URL', request.build_absolute_uri('/').rstrip('/'))
    callback_url = f"{site_url}/payment/callback/"
    redirect_url = callback_url

    external_ref = order_ref or f'ORDER-{request.user.id}-{amount_pesewas}'

    link, reference, error = adapter.generate_embed_link(
        amount_pesewas=amount_pesewas,
        email=email,
        phone=phone,
        external_ref=external_ref,
        callback_url=callback_url,
        redirect_url=redirect_url,
    )

    if error:
        return JsonResponse({'ok': False, 'error': error}, status=502)

    # Stash pending reference + amount in session for verification on return
    request.session['paystack_pending_ref'] = reference
    request.session['paystack_pending_pesewas'] = str(amount_pesewas)

    return JsonResponse({'ok': True, 'link': link, 'reference': reference})

def paystack_callback(request):
    """
    GET landing page for Paystack's dashboard "Callback URL".

    The normal flow uses the inline popup + AJAX verify, so this page is a
    SAFETY NET for cases where Paystack performs a full browser redirect
    (common on mobile money / mobile browsers): Paystack appends
    ?reference=xxxx&trxref=xxxx to this URL after payment.

    Behaviour:
      1. Verify the reference server-side via the Paystack adapter.
      2. If an order already exists with this payment_reference → send the
         customer straight to its Order Received page.
      3. If the payment is verified but no order exists yet (browser
         redirected away before the checkout form could submit), anchor the
         verified ref in the session, alert staff, and show a friendly
         "payment received" page with the reference.
    """
    from .payments import dispatch as _dispatch

    reference = (request.GET.get('reference') or request.GET.get('trxref') or '').strip()[:100]
    if not reference:
        messages.error(request, 'No payment reference was supplied.')
        return redirect('cart')

    # If the order already exists (AJAX flow completed, or webhook landed
    # first), just show it. Match on the logged-in user when possible.
    order_qs = Order.objects.filter(payment_reference=reference)
    if request.user.is_authenticated:
        own = order_qs.filter(user=request.user).first()
        if own:
            return redirect('order_confirmation', order_id=own.id)

    adapter, _ps = _dispatch.gateway_by_provider('paystack')
    verified = False
    amount_pesewas = 0
    error_message = ''
    if adapter is not None:
        result = adapter.verify_payment(reference)
        verified = bool(result.success and result.is_paid)
        amount_pesewas = result.amount_pesewas if verified else 0
        if not verified:
            error_message = result.error_message or 'Transaction not successful.'
    else:
        error_message = 'Paystack is not configured.'

    if verified:
        # Anchor in session so a subsequent checkout POST can match it.
        request.session['paystack_verified_ref']     = reference
        request.session['paystack_verified_pesewas'] = str(amount_pesewas)

        # No order yet — payment landed but the checkout form never submitted.
        # Alert staff so they can reconcile; show the customer a calm page.
        if not order_qs.exists():
            try:
                from django.contrib.auth.models import User as _SU
                who = request.user.username if request.user.is_authenticated else 'guest'
                for staff in _SU.objects.filter(is_staff=True, is_active=True):
                    _notify(
                        user=staff,
                        notif_type='stock_alert',
                        title='⚠️ Paystack payment received — no order yet',
                        message=(
                            f'Reference {reference} was verified via the callback '
                            f'page (GH₵ {amount_pesewas/100:.2f}) but no order has '
                            f'been created. Customer: {who}. The customer may retry '
                            f'checkout, or reconcile manually.'
                        ),
                        link='/panel/orders/',
                    )
            except Exception:
                pass

    return render(request, 'mall/payment_callback.html', {
        'reference':     reference,
        'verified':      verified,
        'error_message': error_message,
        'amount_ghs':    (amount_pesewas / 100) if amount_pesewas else None,
    })


@require_POST
def paystack_webhook(request):
    """
    Receive server-to-server charge.success events from Paystack.

    Thin wrapper around the generic provider webhook dispatcher — kept at
    its original URL (/paystack/webhook/) so existing Paystack dashboard
    configurations don't need to be re-pointed.
    """
    return _handle_provider_webhook(request, 'paystack')


@csrf_exempt
@require_POST
def provider_webhook(request, provider):
    """
    Generic provider webhook endpoint. Routes /payments/<provider>/webhook/
    to the right adapter via the dispatcher.

    Adding a new gateway means: implement handle_webhook() on the adapter
    and add a Paystack-dashboard equivalent pointing at this URL. No view
    changes needed.
    """
    return _handle_provider_webhook(request, provider)


def _handle_provider_webhook(request, provider_slug):
    """
    Common webhook handler. Looks up the adapter, asks it to validate +
    parse the payload, and (for payment events) marks the matching order
    paid, idempotently.
    """
    from .payments import dispatch as _dispatch

    adapter, ps = _dispatch.gateway_by_provider(provider_slug)
    if adapter is None:
        # No PaymentSettings row at all — but webhooks may arrive for
        # historical configs. Don't 500; let the gateway stop retrying.
        return JsonResponse(
            {'status': 'ignored', 'error': f'No {provider_slug} configuration on file.'},
            status=200,
        )

    result = adapter.handle_webhook(request)

    # Always log webhook activity for audit + debugging
    logger.info(
        'Webhook %s event=%r success=%s payment=%s ref=%r status=%s',
        provider_slug, result.event_type, result.success,
        result.is_payment_event, result.reference, result.http_status,
    )

    # Signature/parse failure — return whatever the adapter said
    if not result.success:
        return JsonResponse(
            {'error': result.error_message or 'Webhook rejected.'},
            status=result.http_status,
        )

    # Acknowledge non-payment events without touching orders
    if not result.is_payment_event:
        return JsonResponse({'status': result.response_body or 'ok'}, status=result.http_status)

    # Payment event — mark the order paid if we can find it and it's not already paid.
    # Idempotent: filter on paid=False so a webhook retry can't double-process.
    try:
        order = Order.objects.get(payment_reference=result.reference, paid=False)
    except Order.DoesNotExist:
        # Already paid or unknown reference — safe to ignore. Webhooks can
        # arrive AFTER the customer's browser has already verified, in which
        # case the order is already paid and there's nothing to do.
        return JsonResponse({'status': 'ok — already processed or unknown ref'}, status=200)

    # Anti-tamper: paid amount must cover the order total
    order_pesewas = int(order.total_price * 100)
    if result.amount_pesewas < order_pesewas:
        logger.warning(
            'Webhook %s underpayment for order %s: paid=%s pesewas, owed=%s pesewas',
            provider_slug, order.order_number, result.amount_pesewas, order_pesewas,
        )
        return JsonResponse(
            {'status': 'ignored', 'error': 'Underpayment.'},
            status=200,
        )

    order.paid   = True
    order.status = 'processing'
    order.save(update_fields=['paid', 'status'])

    # Trigger the same post-order notifications as the checkout flow.
    # All of these are best-effort — a notification failure can't block
    # the webhook from acknowledging.
    try:
        send_order_receipt(order)
    except Exception as e:
        logger.warning('Order receipt send failed for %s: %s', order.order_number, e)
    try:
        _notify_admins_new_order(order)
        _notify_customer_status_change(order)
    except Exception as e:
        logger.warning('Post-payment notifications failed for %s: %s', order.order_number, e)
    try:
        from . import whatsapp as _wa
        _wa.notify_admin_new_order(order)
        _wa.notify_customer_new_order(order)
    except Exception as e:
        logger.warning('WhatsApp post-payment notifications failed for %s: %s', order.order_number, e)

    # Auto-issue the first handoff code (admin_to_officer) so the
    # fulfillment officer can start working immediately. This is the key
    # difference from the previous behaviour: when a customer paid via
    # Paystack and the webhook fired but the customer's browser never
    # called /verify/, the order would sit in "processing" with no code
    # issued, blocking fulfilment until an admin noticed.
    _autostart_handoff_chain(order)

    return JsonResponse({'status': result.response_body or 'ok'}, status=200)

# ─── Delivery Fee API ────────────────────────────────────────────────────────

@require_POST
@login_required
def delivery_fee_api(request):
    """
    Calculate home delivery fee and estimated time based on:
    - The selected branch (source)
    - The customer's delivery location. If GPS coordinates from the
      "Pin your location" button are supplied, the fee is computed directly
      from those coords (most precise). Otherwise the typed address is
      geocoded via OpenStreetMap Nominatim.
    Returns JSON: {ok, fee, fee_display, eta_minutes, eta_display, distance_km}
    """
    try:
        body  = json.loads(request.body)
        branch_id = int(body.get('branch_id', 0))
        address   = str(body.get('address', '')).strip()[:300]
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)

    # Optional pinned GPS coordinates (preferred when present & valid).
    pin_lat = pin_lng = None
    try:
        if body.get('lat') is not None and body.get('lng') is not None:
            _plat = float(body.get('lat'))
            _plng = float(body.get('lng'))
            if (-3.5 <= _plng <= 1.5) and (4.5 <= _plat <= 11.5):
                pin_lat, pin_lng = _plat, _plng
    except (TypeError, ValueError):
        pin_lat = pin_lng = None

    # We need either a pinned location or a typed address to work with.
    if not address and pin_lat is None:
        return JsonResponse({'ok': False, 'error': 'No location provided.'})

    try:
        branch = Branch.objects.get(id=branch_id, is_active=True)
    except Branch.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Branch not found.'})

    # If branch has no GPS coords, fall back to region fee
    if branch.latitude is None or branch.longitude is None:
        from .models import REGION_FEES, DELIVERY_BASE_FEE
        fee = REGION_FEES.get(branch.region, DELIVERY_BASE_FEE)
        return JsonResponse({
            'ok': True,
            'fee': float(fee),
            'fee_display': f'GH₵ {fee:.2f}',
            'eta_minutes': 60,
            'eta_display': 'Within 24 hours',
            'distance_km': None,
            'note': 'Distance-based pricing not available for this branch.',
        })

    # ── Preferred path: compute the fee straight from the pinned location ──
    # No geocoding needed — the GPS pin already tells us exactly where the
    # customer is, so this is both more accurate and more reliable.
    if pin_lat is not None:
        distance_km = branch.distance_to(pin_lat, pin_lng)
        fee, eta_minutes = calculate_delivery_fee(distance_km)
        request.session['delivery_lat']          = str(pin_lat)
        request.session['delivery_lng']          = str(pin_lng)
        request.session['delivery_fee_confirmed'] = str(fee)
        request.session['delivery_branch_id']     = str(branch_id)
        if eta_minutes < 60:
            eta_display = f'~{eta_minutes} minutes'
        else:
            hours = eta_minutes // 60
            mins  = eta_minutes % 60
            eta_display = f'~{hours}h {mins}min' if mins else f'~{hours} hour{"s" if hours > 1 else ""}'
        return JsonResponse({
            'ok': True,
            'fee': float(fee),
            'fee_display': f'GH₵ {fee:.2f}',
            'eta_minutes': eta_minutes,
            'eta_display': 'Within 24 hours',
            'distance_km': round(distance_km * 1.3, 1),  # road distance estimate
            'note': 'Fee based on your pinned location.',
        })

    # Geocode the delivery address using OpenStreetMap Nominatim
    try:
        query = urllib.parse.urlencode({
            'q': address + ', Ghana',
            'format': 'json',
            'limit': 1,
            'countrycodes': 'gh',
        })
        geo_url = f'https://nominatim.openstreetmap.org/search?{query}'
        geo_req = urllib.request.Request(
            geo_url,
            headers={'User-Agent': 'HoneyCaveMarket/1.0 (contact@honeycavemarket.com)'},
        )
        with urllib.request.urlopen(geo_req, timeout=5) as resp:
            geo_data = json.loads(resp.read().decode())
    except Exception:
        # Geocoding failed — fall back to region fee
        from .models import REGION_FEES, DELIVERY_BASE_FEE
        fee = REGION_FEES.get(branch.region, DELIVERY_BASE_FEE)
        return JsonResponse({
            'ok': True,
            'fee': float(fee),
            'fee_display': f'GH₵ {fee:.2f}',
            'eta_minutes': 60,
            'eta_display': 'Within 24 hours',
            'distance_km': None,
            'note': 'Could not locate address precisely. Regional rate applied.',
        })

    if not geo_data:
        from .models import REGION_FEES, DELIVERY_BASE_FEE
        fee = REGION_FEES.get(branch.region, DELIVERY_BASE_FEE)
        return JsonResponse({
            'ok': True,
            'fee': float(fee),
            'fee_display': f'GH₵ {fee:.2f}',
            'eta_minutes': 60,
            'eta_display': 'Within 24 hours',
            'distance_km': None,
            'note': 'Address not found on map. Regional rate applied.',
        })

    dest_lat = float(geo_data[0]['lat'])
    dest_lng = float(geo_data[0]['lon'])

    # Store coords in session so checkout POST can use them server-side
    request.session['delivery_lat']  = str(dest_lat)
    request.session['delivery_lng']  = str(dest_lng)

    distance_km = branch.distance_to(dest_lat, dest_lng)
    fee, eta_minutes = calculate_delivery_fee(distance_km)
    # Store the confirmed fee so checkout POST can use it directly
    request.session['delivery_fee_confirmed'] = str(fee)
    request.session['delivery_branch_id']     = str(branch_id)

    # Human-readable ETA
    if eta_minutes < 60:
        eta_display = f'~{eta_minutes} minutes'
    else:
        hours = eta_minutes // 60
        mins  = eta_minutes % 60
        eta_display = f'~{hours}h {mins}min' if mins else f'~{hours} hour{"s" if hours > 1 else ""}'

    return JsonResponse({
        'ok': True,
        'fee': float(fee),
        'fee_display': f'GH₵ {fee:.2f}',
        'eta_minutes': eta_minutes,
        'eta_display': 'Within 24 hours',
        'distance_km': round(distance_km * 1.3, 1),  # road distance estimate
    })


# ─── Reverse Geocoding API ────────────────────────────────────────────────────

@require_POST
@login_required
def reverse_geocode_api(request):
    """
    Turn GPS coordinates into a human-readable address — SERVER-SIDE.

    Why this exists: the checkout page used to call OpenStreetMap Nominatim
    directly from the browser. Browsers silently drop the `User-Agent` header
    (it's a forbidden header), and Nominatim rejects/limits anonymous browser
    requests, so the lookup constantly failed with
    "couldn't look up address automatically". Doing the lookup here — same
    origin (no CORS), with a proper identifying User-Agent and language header —
    is far more reliable. We also return the nearest branch so the pickup flow
    can update in real time.

    Body (JSON): {lat, lng}
    Returns: {ok, found, address, landmark, nearest_branch}
    """
    try:
        body = json.loads(request.body)
        lat  = float(body.get('lat'))
        lng  = float(body.get('lng'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid coordinates.'}, status=400)

    # Ghana bounding box — keep parity with the rest of the checkout flow.
    if not (-3.5 <= lng <= 1.5 and 4.5 <= lat <= 11.5):
        return JsonResponse({'ok': False, 'error': 'Coordinates outside Ghana.'}, status=400)

    address_str = ''
    landmark    = ''
    try:
        query = urllib.parse.urlencode({
            'format':         'json',
            'lat':            f'{lat:.6f}',
            'lon':            f'{lng:.6f}',
            'zoom':           18,
            'addressdetails': 1,
        })
        geo_url = f'https://nominatim.openstreetmap.org/reverse?{query}'
        geo_req = urllib.request.Request(
            geo_url,
            headers={
                'User-Agent':      'HoneyCaveMarket/1.0 (contact@honeycavemarket.com)',
                'Accept-Language': 'en',
            },
        )
        with urllib.request.urlopen(geo_req, timeout=7) as resp:
            data = json.loads(resp.read().decode())

        a = (data.get('address') or {}) if isinstance(data, dict) else {}
        parts = []
        if a.get('house_number') and a.get('road'):
            parts.append(f"{a['house_number']} {a['road']}")
        elif a.get('road'):
            parts.append(a['road'])
        if a.get('neighbourhood'):
            parts.append(a['neighbourhood'])
        elif a.get('suburb'):
            parts.append(a['suburb'])
        elif a.get('residential'):
            parts.append(a['residential'])
        city = (a.get('city') or a.get('town') or a.get('village')
                or a.get('municipality') or a.get('county') or '')
        if city:
            parts.append(city)
        if a.get('state'):
            parts.append(a['state'])

        address_str = ', '.join(parts) or (data.get('display_name', '') if isinstance(data, dict) else '')
        landmark = (a.get('amenity') or a.get('shop') or a.get('tourism')
                    or a.get('leisure') or a.get('building') or '')
    except Exception:
        # Network/geocoder failure — we still return ok:True with an empty
        # address. The customer can type it themselves; coords are kept by JS.
        address_str = ''
        landmark    = ''

    nb = _nearest_branch(lat, lng)
    nearest = None
    if nb is not None:
        dist = nb.distance_to(lat, lng)
        nearest = {
            'id':                  nb.id,
            'name':                nb.name,
            'address':             nb.address,
            'city':                nb.city,
            'phone':               nb.phone,
            'opening_hours':       nb.opening_hours,
            'landmark':            nb.landmark,
            'region':              nb.region,
            'region_display':      nb.get_region_display(),
            'branch_type':         nb.branch_type,
            'branch_type_display': nb.get_branch_type_display(),
            'latitude':            nb.latitude,
            'longitude':           nb.longitude,
            'distance_km':         round(dist, 1) if dist != float('inf') else None,
        }

    return JsonResponse({
        'ok':             True,
        'found':          bool(address_str),
        'address':        address_str,
        'landmark':       landmark,
        'nearest_branch': nearest,
    })


# ─── Checkout ─────────────────────────────────────────────────────────────────

def reconcile_cart_with_branch(cart_items, branch):
    """
    Given cart_items (list of {product, quantity, total} dicts) and a Branch,
    return a new list where each line is annotated with branch-specific
    pricing/availability info.

    Each returned line is the original dict plus:
        'branch_price'   — Decimal price at this branch (== product.price if no override)
        'branch_stock'   — int stock at this branch (0 if branch doesn't carry it)
        'available_here' — bool, True if this branch sells the product
        'available_at'   — list of {id, name, region} branches that DO sell it (only set when available_here is False)
        'branch_total'   — Decimal qty × branch_price
        'has_stock'      — bool, True if branch_stock >= quantity

    Pricing rule (Combo 3 + Option C):
      - Branch sells product = BranchProduct row exists AND is_available
      - No row OR not available = branch doesn't sell it; suggest other branches

    Returns: (annotated_items, new_subtotal, all_available_bool)
        annotated_items — list with annotations above
        new_subtotal    — sum of branch_total across AVAILABLE items
        all_available   — True only if every line is in stock at the branch
    """
    if branch is None or not cart_items:
        return cart_items, sum(i['total'] for i in cart_items), False

    # Single query for all products in the cart at this branch
    product_ids = [i['product'].id for i in cart_items]
    branch_rows = {
        bp.product_id: bp
        for bp in BranchProduct.objects.filter(
            product_id__in=product_ids, branch=branch,
        )
    }

    # For items not stocked here, look up which branches DO have them
    missing_ids = [pid for pid in product_ids if pid not in branch_rows or not branch_rows[pid].is_available]
    available_at_lookup = {}
    if missing_ids:
        elsewhere = (BranchProduct.objects
                     .filter(product_id__in=missing_ids, is_available=True, stock__gt=0)
                     .select_related('branch')
                     .order_by('branch__name'))
        for bp in elsewhere:
            available_at_lookup.setdefault(bp.product_id, []).append({
                'id':     bp.branch_id,
                'name':   bp.branch.name,
                'region': bp.branch.get_region_display(),
                'price':  str(bp.price),
            })

    annotated = []
    new_subtotal = Decimal('0')
    all_available = True

    for item in cart_items:
        product = item['product']
        qty     = item['quantity']
        bp      = branch_rows.get(product.id)
        is_avail = bool(bp and bp.is_available)
        line = dict(item)  # shallow copy
        if is_avail:
            line['branch_price']   = bp.price
            line['branch_stock']   = bp.stock
            line['available_here'] = True
            line['available_at']   = []
            line['branch_total']   = bp.price * qty
            line['has_stock']      = bp.stock >= qty
            new_subtotal += line['branch_total']
            if not line['has_stock']:
                all_available = False
        else:
            line['branch_price']   = product.price  # fallback display
            line['branch_stock']   = 0
            line['available_here'] = False
            line['available_at']   = available_at_lookup.get(product.id, [])
            line['branch_total']   = Decimal('0')   # not counted in total since not buying it here
            line['has_stock']      = False
            all_available = False
        annotated.append(line)

    return annotated, new_subtotal, all_available


@login_required
def cart_branch_check(request):
    """
    JSON endpoint called by the checkout page after the customer picks a
    branch. Returns each cart line annotated with branch availability +
    where else the unavailable items can be found.

    Now also returns `auto_switch_suggestion`: if the requested branch
    can't fulfill the order BUT another branch in the SAME region can,
    we suggest that branch so the page can swap silently. We never
    suggest a branch in a different region — that would change the
    customer's delivery context (different shipping fee, different
    pickup location), so they always make that decision manually.

    GET /api/cart/branch-check/?branch_id=<id>
    Response: { items: [...], subtotal, all_available, auto_switch_suggestion }
    """
    try:
        branch_id = int(request.GET.get('branch_id', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid branch_id'}, status=400)
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    if branch is None:
        return JsonResponse({'error': 'Branch not found'}, status=404)
    cart_items, _ = get_cart_details(request)
    annotated, subtotal, all_avail = reconcile_cart_with_branch(cart_items, branch)

    # ── Auto-switch suggestion (same-region only) ──────────────────────
    # If this branch can't fulfill all items, look for another branch in
    # the SAME region that CAN fulfill all of them. If found, suggest it.
    auto_switch = None
    if not all_avail and cart_items:
        product_ids = [it['product'].id for it in cart_items]
        # Candidate branches: same region, active, NOT the current one
        candidate_branches = Branch.objects.filter(
            region=branch.region, is_active=True,
        ).exclude(pk=branch.pk)

        for candidate in candidate_branches:
            cand_annotated, cand_subtotal, cand_all = reconcile_cart_with_branch(
                cart_items, candidate,
            )
            if cand_all:
                auto_switch = {
                    'branch_id':   candidate.id,
                    'branch_name': candidate.name,
                    'subtotal':    str(cand_subtotal),
                    'reason':      f'{branch.name} is missing items — {candidate.name} has all items in stock.',
                }
                break  # take the first match — they're all same-region equivalents

    return JsonResponse({
        'branch_id':     branch.id,
        'branch_name':   branch.name,
        'all_available': all_avail,
        'subtotal':      str(subtotal),
        'auto_switch_suggestion': auto_switch,
        'items': [
            {
                'product_id':     it['product'].id,
                'product_name':   it['product'].name,
                'quantity':       it['quantity'],
                'available_here': it['available_here'],
                'has_stock':      it['has_stock'],
                'branch_price':   str(it['branch_price']),
                'branch_total':   str(it['branch_total']),
                'branch_stock':   it['branch_stock'],
                'available_at':   it['available_at'],
            }
            for it in annotated
        ],
    })


def _get_cart_for_checkout(request):
    """
    Build cart for checkout POST without capping quantities against current stock.
    Stock reservation may have already decremented stock, so capping here would
    silently drop items from the order. The explicit fresh_stock check later in
    checkout() handles the real stock validation atomically.
    """
    cart = get_cart(request)
    if not cart:
        return [], Decimal('0')
    try:
        product_ids = [int(pid) for pid in cart.keys()]
    except (ValueError, TypeError):
        return [], Decimal('0')

    # Fetch products including those with stock=0 (reserved stock may be 0 now)
    products_by_id = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids)  # no available=True filter here
    }
    cart_items, subtotal = [], Decimal('0')
    for product_id, qty in cart.items():
        try:
            product = products_by_id.get(int(product_id))
            if product is None:
                continue
            qty = max(1, min(int(qty), 99))  # cap only against 99, NOT against stock
            item_total = product.price * qty
            subtotal  += item_total
            cart_items.append({'product': product, 'quantity': qty, 'total': item_total})
        except (ValueError, OverflowError):
            pass
    return cart_items, subtotal


@login_required
def checkout(request):
    cart = get_cart(request)
    if not cart:
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart')

    # On POST (form submission after Paystack): use uncapped cart quantities
    # On GET (page load): use normal display cart with stock caps for UI accuracy
    if request.method == 'POST':
        cart_items, subtotal = _get_cart_for_checkout(request)
    else:
        cart_items, subtotal = get_cart_details(request)
    payment_settings = PaymentSettings.objects.filter(is_active=True)

    branches_qs = Branch.objects.filter(is_active=True).order_by('region', 'name')
    branches_by_region = {}
    for b in branches_qs:
        branches_by_region.setdefault(b.region, []).append({
            'id': b.id, 'name': b.name, 'address': b.address,
            'city': b.city, 'phone': b.phone,
            'opening_hours': b.opening_hours, 'landmark': b.landmark,
            'branch_type': b.branch_type,
            'branch_type_display': b.get_branch_type_display(),
            'latitude': b.latitude, 'longitude': b.longitude,
        })

    selected_region    = request.POST.get('region', 'greater_accra')
    valid_regions      = {k for k, _ in REGION_CHOICES}
    if selected_region not in valid_regions:
        selected_region = 'greater_accra'

    selected_branch_id = request.POST.get('branch_id', '')

    nearest_branch = None
    user_profile   = None
    try:
        user_profile = request.user.profile
        if user_profile.nearest_branch and request.method == 'GET':
            nearest_branch     = user_profile.nearest_branch
            selected_branch_id = str(nearest_branch.id)
            selected_region    = nearest_branch.region
    except UserProfile.DoesNotExist:
        pass

    # ── Auto-assigned pickup point ──────────────────────────────────────────
    # Branch Pickup no longer requires the customer to choose a branch. After
    # payment the order is routed to a branch officer for processing, so we
    # just assign a default pickup point and SHOW the customer where to
    # collect (with directions). They can still optionally override it.
    pickup_branch = nearest_branch or _default_pickup_branch(user_profile)
    if request.method == 'GET' and not selected_branch_id and pickup_branch:
        selected_branch_id = str(pickup_branch.id)
        selected_region    = pickup_branch.region

    # Pickup is free by default; delivery fee calculated when address is entered
    shipping_fee = Decimal('0.00')
    grand_total  = subtotal + shipping_fee

    if request.method == 'POST':
        if not check_rate_limit('checkout', request, limit=5, window=600):
            messages.error(request, 'Too many checkout attempts. Please wait a few minutes.')
            return redirect('cart')

        form = CheckoutForm(request.POST)

        # FIX-POSTPAY: If a Paystack reference came in on this POST, payment
        # has ALREADY been taken. We must NOT silently re-render the checkout
        # page on form/branch errors — the customer will not understand why
        # nothing happened and may pay again. Flag the problem loudly, notify
        # admins so they can manually reconcile, and send the customer to a
        # safe page with their reference. The session still holds
        # paystack_verified_ref/pesewas so a support agent can look it up.
        submitted_ref_raw = request.POST.get('payment_reference', '').strip()
        paystack_ref_in_session = request.session.get('paystack_verified_ref', '')
        payment_already_taken = bool(
            submitted_ref_raw
            and (
                (paystack_ref_in_session and submitted_ref_raw == paystack_ref_in_session)
            )
        )

        def _flag_stranded_payment(reason):
            # Notify every active staff user so someone picks it up quickly.
            try:
                from django.contrib.auth.models import User as _SU
                for staff in _SU.objects.filter(is_staff=True, is_active=True):
                    _notify(
                        user=staff,
                        notif_type='stock_alert',
                        title=f'⚠️ Payment received but order not created',
                        message=(
                            f'Paystack ref {submitted_ref_raw} was verified and '
                            f'charged, but the checkout form failed ({reason}). '
                            f'Customer: {request.user.username} ({request.user.email}). '
                            f'Please reconcile manually.'
                        ),
                        link='/panel/orders/',
                    )
            except Exception:
                pass
            messages.error(
                request,
                f'Your payment was received (reference: {submitted_ref_raw}) but we '
                f'could not complete the order automatically. Our team has been '
                f'notified and will contact you shortly. Please keep this reference.'
            )

        if form.is_valid():
            # Branch Pickup no longer needs a manually-chosen branch. If none
            # came through (JS disabled, or the customer didn't override the
            # auto-assigned pickup point), assign a sensible default now so the
            # order can still be created and routed to a branch officer.
            if not selected_branch_id:
                _fallback = _default_pickup_branch(getattr(request.user, 'profile', None))
                if _fallback is not None:
                    selected_branch_id = str(_fallback.id)

            if not selected_branch_id:
                if payment_already_taken:
                    _flag_stranded_payment('no branch selected')
                    return redirect('product_list')
                messages.error(request, 'Please select a Market branch to collect your order from.')
            else:
                try:
                    branch = Branch.objects.get(id=selected_branch_id, is_active=True)

                    # ── Per-branch pricing reconciliation (Combo 3) ──
                    # Recompute subtotal using BranchProduct.price for the
                    # picked branch. Refuses to proceed if any cart item is
                    # not stocked at this branch (Option C).
                    annotated, branch_subtotal, all_avail = reconcile_cart_with_branch(
                        cart_items, branch,
                    )
                    if not all_avail:
                        # Do NOT block the order. Notify the branch officer to source
                        # any items not currently in stock, then let the order proceed.
                        unavail_names = ', '.join(
                            it['product'].name for it in annotated
                            if not it.get('available_here', it.get('has_stock', True)) is True
                        )
                        try:
                            officer = getattr(branch, 'fulfillment_officer', None)
                            if officer and not officer.is_active:
                                officer = None
                            if officer is None:
                                try:
                                    from .models import BranchAssignment
                                    pa = (BranchAssignment.objects
                                          .filter(branch=branch, role='primary',
                                                  status='approved', officer__is_active=True)
                                          .select_related('officer').first())
                                    officer = pa.officer if pa else None
                                    if officer is None:
                                        sa = (BranchAssignment.objects
                                              .filter(branch=branch, status='approved',
                                                      officer__is_active=True)
                                              .select_related('officer').first())
                                        officer = sa.officer if sa else None
                                except Exception:
                                    pass
                            notif_msg = (
                                f'An order was placed and the following items need sourcing '
                                f'at {branch.name}: {unavail_names}. '
                                f'Please arrange to fulfil them for the customer.'
                            )
                            if officer:
                                _notify(user=officer, notif_type='stock_alert',
                                        title=f'📦 Items to source at {branch.name}',
                                        message=notif_msg, link='/panel/orders/')
                            else:
                                from django.contrib.auth.models import User as _SU
                                for staff in _SU.objects.filter(is_staff=True, is_active=True):
                                    _notify(user=staff, notif_type='stock_alert',
                                            title=f'📦 Items to source at {branch.name}',
                                            message=notif_msg, link='/panel/orders/')
                        except Exception:
                            pass  # never let notification failure break the order
                    # Use per-branch prices; unavailable items fall back to catalogue price
                    cart_items = [
                        {
                            'product':    it['product'],
                            'quantity':   it['quantity'],
                            'total':      it.get('branch_total') or it['product'].price * it['quantity'],
                            'branch_price': it.get('branch_price', it['product'].price),
                        }
                        for it in annotated
                    ]
                    subtotal = sum(i['total'] for i in cart_items)

                    # Re-validate shipping fee server-side — never trust client value.
                    # Pickup is always free.
                    # Delivery: use the fee confirmed by delivery_fee_api (stored in session).
                    # If the session has a confirmed fee for this branch, use it.
                    # Otherwise recalculate from GPS coords, or fall back to region rate.
                    fulfillment = form.cleaned_data.get('fulfillment_type', 'pickup')
                    if fulfillment == 'pickup':
                        server_shipping_fee = Decimal('0.00')
                    else:
                        confirmed_fee    = request.session.get('delivery_fee_confirmed', '')
                        confirmed_branch = request.session.get('delivery_branch_id', '')
                        delivery_lat     = request.session.get('delivery_lat')
                        delivery_lng     = request.session.get('delivery_lng')

                        if confirmed_fee and confirmed_branch == str(branch.id):
                            # Use the fee already calculated and shown to the customer
                            try:
                                server_shipping_fee = Decimal(confirmed_fee)
                            except Exception:
                                server_shipping_fee = DELIVERY_BASE_FEE
                        elif delivery_lat and delivery_lng and branch.latitude and branch.longitude:
                            # Recalculate from saved GPS coords
                            dist_km = branch.distance_to(float(delivery_lat), float(delivery_lng))
                            server_shipping_fee, _ = calculate_delivery_fee(dist_km)
                        else:
                            # Last resort: region-based flat fee
                            server_shipping_fee = REGION_FEES.get(branch.region, DELIVERY_BASE_FEE)
                    server_grand_total  = subtotal + server_shipping_fee

                    # FIX-PROMO: Re-validate the promo code stored in the session
                    # and subtract the discount from the server-computed total.
                    # The AJAX apply_promo_code view only previews the discount —
                    # the actual deduction MUST happen here, server-side.
                    applied_promo  = None
                    promo_discount = Decimal('0')
                    promo_code_str = request.session.get('promo_code', '').strip().upper()
                    if promo_code_str:
                        try:
                            promo_obj = PromoCode.objects.get(code=promo_code_str)
                            valid, _  = promo_obj.is_valid()
                            if valid and subtotal >= promo_obj.min_order_value:
                                promo_discount  = promo_obj.calculate_discount(subtotal)
                                server_grand_total = max(Decimal('0'), server_grand_total - promo_discount)
                                applied_promo   = promo_obj
                        except PromoCode.DoesNotExist:
                            pass  # code no longer exists — ignore silently

                    # FIX-PAYSTACK: If a payment reference was submitted,
                    # confirm the server-verified amount (stored in session by
                    # An attacker cannot pay ₵1, get a verified ref, then submit a
                    # ₵500 order — checkout() compares pesewas-for-pesewas.
                    submitted_ref   = form.cleaned_data.get('payment_reference', '').strip()
                    paystack_ref    = request.session.get('paystack_verified_ref', '')
                    paystack_amount_pesewas = int(request.session.get('paystack_verified_pesewas', '0'))
                    # Convert server total to pesewas for comparison (1 GHS = 100 pesewas)
                    server_total_pesewas = int(server_grand_total * 100)
                    # Detect payment path: JS sets the hidden field AND session ref must match
                    if submitted_ref:
                        # Check Paystack path
                        if paystack_ref and submitted_ref == paystack_ref:
                            verified_amount_pesewas = paystack_amount_pesewas
                        else:
                            verified_amount_pesewas = 0

                        if verified_amount_pesewas < server_total_pesewas:
                            messages.error(
                                request,
                                'Payment verification failed or amount mismatch. '
                                'Please complete payment again.'
                            )
                            return redirect('cart')

                    order                  = form.save(commit=False)
                    order.user             = request.user
                    order.branch           = branch
                    order.region           = branch.region
                    order.shipping_fee     = server_shipping_fee
                    order.total_price      = server_grand_total
                    order.discount_amount  = promo_discount
                    order.promo_code       = applied_promo
                    order.paid             = True
                    order.fulfillment_type   = form.cleaned_data.get('fulfillment_type', 'pickup')
                    order.delivery_address   = sanitize_text(form.cleaned_data.get('delivery_address', ''), 500)
                    order.delivery_landmark  = sanitize_text(form.cleaned_data.get('delivery_landmark', ''), 200)
                    # GPS pin (already validated as inside-Ghana by the
                    # form's clean(); will be None if validation rejected
                    # an out-of-bounds reading or only one half was set).
                    order.delivery_lat       = form.cleaned_data.get('delivery_lat')
                    order.delivery_lng       = form.cleaned_data.get('delivery_lng')
                    order.payment_reference  = sanitize_text(form.cleaned_data.get('payment_reference', ''), 100)
                    # BRANCH-AWARE STOCK CHECK: fetch stock from BranchProduct
                    # for the fulfillment branch, not the legacy Product.stock
                    # field. The product might be in stock at branch A but the
                    # customer is ordering from branch B — that mismatch was
                    # the root cause of overselling pre-launch.
                    product_ids = [i['product'].id for i in cart_items]
                    fresh_stock = {
                        bp.product_id: bp.stock
                        for bp in BranchProduct.objects.filter(
                            branch=branch, product_id__in=product_ids,
                        ).only('product_id', 'stock')
                    }
                    for item in cart_items:
                        # Default 0 = not carried at this branch.
                        # If the cart was built against this branch already,
                        # this should never trigger; treat it as a defensive guard.
                        available = fresh_stock.get(item['product'].id, 0)
                        if available < item['quantity']:
                            if available == 0:
                                msg = (
                                    f'Sorry, "{item["product"].name}" is no longer '
                                    f'available at {branch.name}. Please update your cart '
                                    f'or pick a different branch.'
                                )
                            else:
                                msg = (
                                    f'Sorry, "{item["product"].name}" only has '
                                    f'{available} unit(s) left at {branch.name}. '
                                    f'Please update your cart.'
                                )
                            messages.error(request, msg)
                            return redirect('cart')

                    # ── Save order + items + stock in one atomic transaction ──────
                    # Everything must succeed or nothing is committed.
                    # This guarantees: if the order exists in DB, it ALWAYS has items.
                    session_key = request.session.session_key

                    try:
                        with transaction.atomic():
                            # Save the order header first
                            order.save()

                            # Release stock reservations INSIDE the transaction so
                            # stock is restored and immediately re-decremented atomically
                            if session_key:
                                reservations = StockReservation.objects.filter(
                                    session_key=session_key
                                ).select_related('product')
                                for res in reservations:
                                    Product.objects.filter(pk=res.product.pk).update(
                                        stock=F('stock') + res.quantity
                                    )
                                reservations.delete()

                            # Create each OrderItem and decrement stock atomically
                            for item in cart_items:
                                # Use the per-branch price if reconciliation set it
                                # (Combo 3); fall back to catalogue price otherwise.
                                line_price = item.get('branch_price', item['product'].price)
                                OrderItem.objects.create(
                                    order=order,
                                    product=item['product'],
                                    quantity=item['quantity'],
                                    price=line_price,
                                )
                                # Decrement BranchProduct.stock atomically (per-branch tracking)
                                bp_rows_updated = BranchProduct.objects.filter(
                                    product=item['product'], branch=branch,
                                    stock__gte=item['quantity'],
                                ).update(stock=F('stock') - item['quantity'])
                                # Also decrement Product.stock for catalogue accuracy
                                rows_updated = Product.objects.filter(
                                    pk=item['product'].pk,
                                    stock__gte=item['quantity'],
                                ).update(stock=F('stock') - item['quantity'])

                                if not rows_updated:
                                    if submitted_ref:
                                        # Payment taken but stock gone — keep order,
                                        # mark pending, admin will resolve.
                                        # Don't raise — let transaction commit with items so far.
                                        order.status = 'pending'
                                        order.save(update_fields=['status'])
                                        from django.contrib.auth.models import User as _SU
                                        for staff in _SU.objects.filter(is_staff=True, is_active=True):
                                            _notify(
                                                user=staff,
                                                notif_type='stock_alert',
                                                title=f'⚠️ Stock issue — {order.order_number}',
                                                message=(
                                                    f'Payment received for {order.order_number} but '
                                                    f'"{item["product"].name}" ran out mid-checkout. '
                                                    f'Please fulfill manually or refund.'
                                                ),
                                                link=f'/panel/orders/{order.id}/',
                                            )
                                        # Break out of loop — process remaining items best-effort
                                        break
                                    else:
                                        # No payment taken — raise to rollback whole transaction
                                        raise ValueError(f'SOLDOUT:{item["product"].name}')

                            # Promo usage inside transaction so it rolls back if order fails
                            if applied_promo:
                                PromoCode.objects.filter(pk=applied_promo.pk).update(
                                    times_used=F('times_used') + 1
                                )

                    except ValueError as _ve:
                        err_msg = str(_ve)
                        if err_msg.startswith('SOLDOUT:'):
                            product_name = err_msg[len('SOLDOUT:'):]
                            messages.error(
                                request,
                                f'Sorry, "{product_name}" just sold out. '
                                f'Please remove it from your cart and try again.'
                            )
                        else:
                            messages.error(request, 'Could not complete your order. Please try again.')
                        return redirect('cart')

                    send_order_receipt(order)
                    _notify_admins_new_order(order)
                    # WhatsApp alerts — safe no-ops if WhatsApp is not configured.
                    # Wrapped in try/except so a messaging failure never breaks
                    # order creation for the customer.
                    try:
                        from . import whatsapp as _wa
                        _wa.notify_admin_new_order(order)
                        _wa.notify_customer_new_order(order)
                    except Exception as _e:
                        logger.warning('WhatsApp notification failed silently: %s', _e)

                    # ── Auto-start chain-of-custody flow ─────────────────────
                    # Auto-issue the first handoff code so the fulfillment
                    # officer can start working without an admin online.
                    # See _autostart_handoff_chain() for the policy.
                    _autostart_handoff_chain(order)

                    for item in cart_items:
                        _check_stock_alerts(item['product'])
                    # Clear cart and payment/promo session state
                    request.session['cart'] = {}
                    request.session.pop('promo_code', None)
                    request.session.pop('paystack_verified_ref', None)
                    request.session.pop('paystack_verified_pesewas', None)

                    request.session.pop('delivery_lat', None)
                    request.session.pop('delivery_lng', None)
                    request.session.pop('delivery_fee_confirmed', None)
                    request.session.pop('delivery_branch_id', None)
                    messages.success(request, f'Order {order.order_number} confirmed! Payment received. Thank you for shopping with us.')
                    # ORDER-RECEIVED: send the customer to the dedicated order
                    # confirmation page (timeline, pickup/delivery code, receipt)
                    # instead of dumping them back on the product list.
                    return redirect('order_confirmation', order_id=order.id)
                except Branch.DoesNotExist:
                    if payment_already_taken:
                        _flag_stranded_payment('invalid branch')
                        return redirect('product_list')
                    messages.error(request, 'Invalid branch selected. Please try again.')
        else:
            # Form did not validate. If a Paystack payment was already captured
            # for this request, we CANNOT just re-render the page silently —
            # the customer's money is gone and they will have no idea why the
            # order did not go through. Flag and reconcile manually.
            if payment_already_taken:
                invalid_fields = ', '.join(form.errors.keys()) or 'unknown fields'
                _flag_stranded_payment(f'form invalid: {invalid_fields}')
                return redirect('product_list')
    else:
        # ── Auto-fill checkout from the user's last successful order ────────
        # Priority chain:
        #   1. Last delivered/dispatched order — has delivery info, region,
        #      fulfillment preference (most accurate; reflects real shipping)
        #   2. Most recent order of any status — fallback if user hasn't
        #      received anything yet
        #   3. UserProfile.phone — for phone field if no orders exist
        #   4. User.email and full_name — always available
        # The customer can edit any pre-filled field before placing the order.
        prior = (Order.objects
                 .filter(user=request.user)
                 .exclude(status__in=['cancelled'])
                 .order_by('-created')
                 .first())
        profile = getattr(request.user, 'profile', None)

        initial = {
            # User-account fields — always available
            'full_name': request.user.get_full_name() or request.user.username,
            'email':     request.user.email,
        }

        if prior:
            # Prefer prior order data (most complete delivery context)
            initial.update({
                'phone':            prior.phone,
                'address':          prior.address,
                'city':             prior.city,
                'zip_code':         prior.zip_code,
                'fulfillment_type': prior.fulfillment_type,
                'delivery_address': prior.delivery_address,
                'region':           prior.region,
            })
            # If they completed it with different name/email, prefer that —
            # they may use a different account email than checkout email.
            if prior.full_name:
                initial['full_name'] = prior.full_name
            if prior.email:
                initial['email'] = prior.email
        else:
            # No prior orders — fall back to profile phone if set
            if profile and profile.phone:
                initial['phone'] = profile.phone
            initial.setdefault('region', 'greater_accra')

        form = CheckoutForm(initial=initial)
        # FEAT-RESERVE: On page load expire old reservations then reserve current
        # cart items for this session so concurrent shoppers can't grab the last unit.
        if request.session.session_key:
            from django.utils import timezone as _tz
            import datetime as _dt
            StockReservation.expire_old()
            expires = _tz.now() + _dt.timedelta(minutes=StockReservation.RESERVATION_MINUTES)
            for item in cart_items:
                qty = item['quantity']
                existing = StockReservation.objects.filter(
                    session_key=request.session.session_key,
                    product=item['product'],
                ).first()
                if existing:
                    # Extend expiry and adjust qty diff against stock
                    diff = qty - existing.quantity
                    if diff != 0:
                        rows = Product.objects.filter(
                            pk=item['product'].pk,
                            stock__gte=diff if diff > 0 else 0,
                        ).update(stock=F('stock') - diff)
                        if rows or diff < 0:
                            existing.quantity = qty
                            existing.expires_at = expires
                            existing.save()
                    else:
                        existing.expires_at = expires
                        existing.save()
                else:
                    rows = Product.objects.filter(
                        pk=item['product'].pk,
                        stock__gte=qty,
                    ).update(stock=F('stock') - qty)
                    if rows:
                        StockReservation.objects.create(
                            session_key=request.session.session_key,
                            product=item['product'],
                            quantity=qty,
                            expires_at=expires,
                        )

    # Detect if the form was auto-filled from a prior order so the template
    # can show a small "filled from your last order" banner.
    autofilled_from_prior = (
        request.method != 'POST'
        and getattr(request.user, 'is_authenticated', False)
        and Order.objects.filter(user=request.user).exclude(status='cancelled').exists()
    )

    return render(request, 'mall/checkout.html', {
        'form':               form,
        'cart_items':         cart_items,
        'subtotal':           subtotal,
        'shipping_fee':       shipping_fee,
        'grand_total':        grand_total,
        'region_fees_json':   json.dumps({k: float(v) for k, v in REGION_FEES.items()}),
        'branches_json':      json.dumps(branches_by_region),
        'region_choices':     REGION_CHOICES,
        'selected_region':    selected_region,
        'selected_branch_id': selected_branch_id,
        'payment_settings':   payment_settings,
        'nearest_branch':     nearest_branch,
        'pickup_branch':      pickup_branch,
        'autofilled_from_prior': autofilled_from_prior,
        'paystack_public_key': django_settings.PAYSTACK_PUBLIC_KEY,
        'paystack_configured': bool(
            django_settings.PAYSTACK_PUBLIC_KEY
            and django_settings.PAYSTACK_PUBLIC_KEY.startswith(('pk_live_', 'pk_test_'))
        ),
    })


@login_required
def order_confirmation(request, order_id):
    # PERF-01 FIX: prefetch items and products in one query so the template
    # and send_order_receipt() don't trigger N+1 queries per order item.
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product', 'handoff_codes'),
        id=order_id, user=request.user,
    )

    # Handoff codes relevant to the customer:
    #   pickup orders     → keeper_to_customer (customer SHOWS this at the branch)
    #   delivery orders   → rider_to_customer  (customer SHOWS this when rider arrives)
    # FLIPPED-FLOW: the customer no longer ENTERS any code on this page.
    # The rider/fulfillment officer enters it on their portal. This page just
    # displays the code for the customer to read aloud or show as QR.
    pickup_code = None
    rider_code  = None

    for h in order.handoff_codes.all():
        if h.stage == 'officer_to_customer':
            if pickup_code is None or h.created_at > pickup_code.created_at:
                pickup_code = h
        elif h.stage == 'rider_to_customer':
            if rider_code is None or h.created_at > rider_code.created_at:
                rider_code = h

    # ── Build the customer-facing tracking timeline ─────────────────────
    # Each milestone is a dict with: key, label, icon, done(bool), at(datetime|None).
    # Order matters — they're rendered in sequence on the page.
    handoff_by_stage = {}
    for h in order.handoff_codes.all():
        existing = handoff_by_stage.get(h.stage)
        if existing is None or h.created_at > existing.created_at:
            handoff_by_stage[h.stage] = h

    rider_delivery_obj = None
    try:
        rider_delivery_obj = order.rider_delivery
    except Exception:
        pass

    keeper_verified = handoff_by_stage.get('admin_to_officer')
    keeper_verified = keeper_verified.used_at if (keeper_verified and keeper_verified.is_verified) else None

    rider_pickup_verified = handoff_by_stage.get('officer_to_rider')
    rider_pickup_verified = rider_pickup_verified.used_at if (rider_pickup_verified and rider_pickup_verified.is_verified) else None

    customer_received = handoff_by_stage.get('rider_to_customer') or handoff_by_stage.get('officer_to_customer')
    customer_received = customer_received.used_at if (customer_received and customer_received.is_verified) else None

    if order.fulfillment_type == 'pickup':
        timeline = [
            {'key': 'placed',    'label': 'Order placed',           'icon': '🛒',
             'done': True, 'at': order.created},
            {'key': 'paid',      'label': 'Payment confirmed',      'icon': '💳',
             'done': True, 'at': order.created},  # we don't store paid_at separately; same as created
            {'key': 'preparing', 'label': 'Fulfillment Officer is preparing your order', 'icon': '📦',
             'done': bool(keeper_verified), 'at': keeper_verified},
            {'key': 'ready',     'label': 'Ready for pickup at branch', 'icon': '🏪',
             'done': bool(handoff_by_stage.get('officer_to_customer')),
             'at': handoff_by_stage.get('officer_to_customer').created_at if handoff_by_stage.get('officer_to_customer') else None},
            {'key': 'collected', 'label': 'Collected by you', 'icon': '✅',
             'done': bool(customer_received), 'at': customer_received},
        ]
    else:
        # Delivery flow — full 6-step timeline
        timeline = [
            {'key': 'placed',     'label': 'Order placed',                        'icon': '🛒',
             'done': True, 'at': order.created},
            {'key': 'paid',       'label': 'Payment confirmed',                   'icon': '💳',
             'done': True, 'at': order.created},
            {'key': 'preparing',  'label': 'Fulfillment Officer is preparing your order', 'icon': '📦',
             'done': bool(keeper_verified), 'at': keeper_verified},
            {'key': 'rider',      'label': 'Rider assigned',                      'icon': '🛵',
             'done': bool(rider_delivery_obj),
             'at': rider_delivery_obj.dispatched_at if rider_delivery_obj else None,
             'detail': rider_delivery_obj.rider_name if rider_delivery_obj else None},
            {'key': 'transit',    'label': 'Out for delivery',                    'icon': '🚚',
             'done': bool(rider_pickup_verified), 'at': rider_pickup_verified},
            {'key': 'delivered',  'label': 'Delivered to you',                    'icon': '🏠',
             'done': bool(customer_received), 'at': customer_received},
        ]

    # Mark the FIRST not-yet-done step as the "current" step for highlighting
    current_marked = False
    for step in timeline:
        if not step['done'] and not current_marked:
            step['current'] = True
            current_marked = True
        else:
            step['current'] = False

    return render(request, 'mall/order_confirmation.html', {
        'order':       order,
        'pickup_code': pickup_code,
        'rider_code':  rider_code,
        'timeline':    timeline,
    })


@login_required
def order_status_json(request, order_id):
    """
    Return a small JSON snapshot of an order's current state — used by the
    customer's order page to poll silently and update the status badge
    without a full page reload.

    Polled every 15 seconds by the page; the response is intentionally tiny
    so the polling cost is negligible even at scale.

    Only the order owner can see their own status. 404 for any other user.
    """
    order = get_object_or_404(
        Order.objects.prefetch_related('handoff_codes'),
        id=order_id, user=request.user,
    )

    # Find current handoff codes by stage so the page can decide whether to
    # show/hide the code-entry box and the QR display.
    pickup_code = None
    rider_code  = None
    for h in order.handoff_codes.all():
        if h.stage == 'officer_to_customer':
            if pickup_code is None or h.created_at > pickup_code.created_at:
                pickup_code = h
        elif h.stage == 'rider_to_customer':
            if rider_code is None or h.created_at > rider_code.created_at:
                rider_code = h

    def _code_summary(c):
        if c is None:
            return None
        return {
            'code':        c.code,
            'is_verified': c.is_verified,
            'locked':      c.locked,
            'is_expired':  c.is_expired,
            'attempts':    c.attempts,
            'remaining':   c.remaining_attempts,
            'used_at':     c.used_at.isoformat() if c.used_at else None,
        }

    return JsonResponse({
        'status':           order.status,
        'status_display':   order.get_status_display(),
        'fulfillment_type': order.fulfillment_type,
        'pickup_code':      _code_summary(pickup_code),
        'rider_code':       _code_summary(rider_code),
    })


# ─── Auth — Register + OTP ────────────────────────────────────────────────────

@rate_limit('register', limit=5, window=300)
def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = RegisterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        otp, plaintext = OTPVerification.create_for(user, purpose='signup')
        send_otp(user, otp, plaintext_code=plaintext)
        request.session['otp_user_id'] = user.id
        request.session['otp_purpose']  = 'signup'
        # Tell the user where the code went so they know which channels to check.
        prof = getattr(user, 'profile', None)
        if prof and prof.phone:
            messages.info(request, f'A 6-digit code was sent to {user.email} and your phone ({prof.phone}).')
        else:
            messages.info(request, f'A 6-digit code has been sent to {user.email}.')
        return redirect('verify_otp')
    return render(request, 'mall/register.html', {'form': form})


def verify_otp(request):
    user_id = request.session.get('otp_user_id')
    purpose = request.session.get('otp_purpose', 'signup')
    if not user_id:
        messages.error(request, 'Session expired. Please try again.')
        return redirect('register')

    user = get_object_or_404(User, id=user_id)
    form = OTPVerifyForm(request.POST or None)

    if request.method == 'POST':
        # SEC-03 FIX: Dual rate limit — both IP-based and user-based counters
        # must pass. This prevents IP-rotation from bypassing per-IP limits.
        if not check_otp_rate_limit(request, user.id, limit=5, window=900):
            messages.error(request, 'Too many attempts. Please wait 15 minutes or request a new code.')
            return render(request, 'mall/verify_otp.html', {'form': form, 'user': user, 'purpose': purpose})

        # SEC-03 FIX: Hard lockout after 10 cumulative failures regardless of IP
        if is_otp_locked_out(user.id):
            messages.error(request, 'This account is temporarily locked due to too many failed attempts. Please request a new code or try again in 1 hour.')
            return render(request, 'mall/verify_otp.html', {'form': form, 'user': user, 'purpose': purpose})

        if form.is_valid():
            entered = form.cleaned_data['otp'].strip()

            if not is_valid_otp(entered):
                messages.error(request, 'Code must be 6 digits.')
                return render(request, 'mall/verify_otp.html', {'form': form, 'user': user, 'purpose': purpose})

            otp_obj = OTPVerification.objects.filter(
                user=user, purpose=purpose, is_used=False
            ).order_by('-created_at').first()

            # `matches()` does the constant-time compare and handles both
            # hashed and legacy-plaintext rows transparently.
            if otp_obj and otp_obj.is_valid() and otp_obj.matches(entered):
                otp_obj.is_used = True
                otp_obj.save()
                clear_otp_rate_limit(request, user.id)
                clear_otp_failures(user.id)

                if purpose == 'signup':
                    user.is_active = True
                    user.save()
                    profile, _ = UserProfile.objects.get_or_create(user=user)
                    profile.is_verified = True
                    profile.save()
                    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                    # Rotate session after login — prevents session fixation
                    request.session.cycle_key()
                    del request.session['otp_user_id']
                    del request.session['otp_purpose']
                    messages.success(request, f'Welcome, {user.first_name or user.username}! Your account is verified.')
                    # Redirect to branch selection so customer shops from their branch
                    return redirect('select_branch')

                elif purpose == 'password_reset':
                    request.session['reset_user_id'] = user.id
                    del request.session['otp_user_id']
                    del request.session['otp_purpose']
                    return redirect('reset_password')
            else:
                record_otp_failure(user.id)
                messages.error(request, 'Invalid or expired code. Please try again.')

    return render(request, 'mall/verify_otp.html', {'form': form, 'user': user, 'purpose': purpose})


@rate_limit('resend_otp', limit=3, window=300)
def resend_otp(request):
    user_id = request.session.get('otp_user_id')
    purpose = request.session.get('otp_purpose', 'signup')
    if not user_id:
        return redirect('register')
    user = get_object_or_404(User, id=user_id)
    OTPVerification.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)
    otp, plaintext = OTPVerification.create_for(user, purpose=purpose)
    send_otp(user, otp, plaintext_code=plaintext)
    messages.success(request, f'A new code has been sent to {user.email}.')
    return redirect('verify_otp')


# ─── Forgot / Reset Password ──────────────────────────────────────────────────

@rate_limit('forgot_password', limit=5, window=300)
def forgot_password(request):
    form = ForgotPasswordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            OTPVerification.objects.filter(user=user, purpose='password_reset', is_used=False).update(is_used=True)
            otp, plaintext = OTPVerification.create_for(user, purpose='password_reset')
            send_otp(user, otp, plaintext_code=plaintext)
            request.session['otp_user_id'] = user.id
            request.session['otp_purpose']  = 'password_reset'
        except User.DoesNotExist:
            pass   # silent — prevent user enumeration
        messages.info(request, 'If that email is registered, a reset code has been sent.')
        return redirect('verify_otp')
    return render(request, 'mall/forgot_password.html', {'form': form})


def reset_password(request):
    user_id = request.session.get('reset_user_id')
    if not user_id:
        messages.error(request, 'Session expired. Please start over.')
        return redirect('forgot_password')
    user = get_object_or_404(User, id=user_id)
    form = ResetPasswordForm(user=user, data=request.POST or None)
    if form.is_valid():
        form.save()
        # FIX-RESET-SESSION: Flush the entire session so no partial state
        # (reset_user_id, OTP purpose, etc.) lingers. cycle_key() alone only
        # rotates the key — old session data stays. flush() wipes everything.
        request.session.flush()
        messages.success(request, 'Password updated! Please sign in.')
        return redirect('login')
    return render(request, 'mall/reset_password.html', {'form': form})


# ─── Login / Logout ───────────────────────────────────────────────────────────

@rate_limit('login', limit=10, window=60)
def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        # Rotate session after login — prevents session fixation attacks
        request.session.cycle_key()
        clear_rate_limit('login', request)
        messages.success(request, f'Welcome back, {user.first_name or user.username}!')
        next_url = request.GET.get('next', '')
        return redirect(safe_redirect_url(next_url, request, fallback='/'))
    return render(request, 'mall/login.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')


# ─── Branch Selection (post-registration) ────────────────────────────────────

@login_required
def select_branch(request):
    """
    Shown after registration — lets the customer choose their preferred branch.
    Saves nearest_branch to their profile and redirects them to the product list
    filtered to that branch's region context.
    """
    branches_qs = Branch.objects.filter(is_active=True).order_by('region', 'name')
    branches_by_region = {}
    for b in branches_qs:
        branches_by_region.setdefault(b.get_region_display(), []).append(b)

    if request.method == 'POST':
        branch_id = request.POST.get('branch_id')
        if branch_id:
            try:
                branch = Branch.objects.get(id=branch_id, is_active=True)
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                profile.nearest_branch = branch
                profile.save()
                messages.success(
                    request,
                    f'Welcome! Your home branch is set to {branch.name}. '
                    f'Start shopping — your branch will be pre-selected at checkout.'
                )
                return redirect('product_list')
            except Branch.DoesNotExist:
                messages.error(request, 'Invalid branch. Please try again.')
        else:
            # User skipped — go straight to products
            return redirect('product_list')

    from .models import REGION_CHOICES as _RC
    import json as _json
    branches_json = {}
    for b in branches_qs:
        branches_json.setdefault(b.region, []).append({
            'id': b.id, 'name': b.name, 'address': b.address,
            'city': b.city, 'phone': b.phone,
            'opening_hours': b.opening_hours, 'landmark': b.landmark,
            'branch_type': b.branch_type,
        })

    return render(request, 'mall/select_branch.html', {
        'branches_by_region': branches_by_region,
        'branches_json': _json.dumps(branches_json),
        'region_choices': REGION_CHOICES,
    })


# ─── Admin Login ──────────────────────────────────────────────────────────────

@rate_limit('admin_login', limit=5, window=300)  # SEC-04: Stricter than customer login (5/5min vs 10/1min)
def admin_login_view(request):
    """
    SEC-04 FIX: Dedicated login page for the /panel/ admin area with its own
    stricter rate limit scope. Separating admin login from the customer login
    means a credential-stuffing attack on /login/ does NOT consume the admin
    rate limit, and vice versa. Staff-only: non-staff users are rejected even
    if their credentials are correct.
    """
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect('admin_dashboard')
        return redirect('home')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        if not user.is_staff:
            # Valid credentials but not a staff user — reject with a generic message
            # to avoid confirming that the account exists
            messages.error(request, 'Invalid credentials or insufficient permissions.')
            return render(request, 'mall/admin_login.html', {'form': AuthenticationForm()})
        clear_rate_limit('admin_login', request)
        # Check if 2FA is enabled for this user
        try:
            from .models import AdminTOTP
            totp = user.totp
            if totp.is_enabled:
                # Don't log in yet — store user id and redirect to 2FA verify
                request.session['needs_2fa_user_id'] = user.id
                return redirect('admin_2fa_verify')
        except Exception:
            pass   # No TOTP record = 2FA not set up, proceed normally
        login(request, user)
        request.session.cycle_key()
        # Audit log
        try:
            ip = (request.META.get('HTTP_X_FORWARDED_FOR','').split(',')[0].strip()
                  or request.META.get('REMOTE_ADDR'))
            AuditLog.objects.create(actor=user, action='admin_login',
                target_repr=f'User "{user.username}"', ip_address=ip)
        except Exception:
            pass
        messages.success(request, f'Welcome, {user.first_name or user.username}.')
        return redirect('admin_dashboard')

    return render(request, 'mall/admin_login.html', {'form': form})


# ─── Profile ──────────────────────────────────────────────────────────────────

@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    orders          = Order.objects.filter(user=request.user).order_by('-created')[:5]

    profile_form   = ProfileUpdateForm(instance=user_profile)
    password_form  = PasswordChangeForm(user=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            profile_form = ProfileUpdateForm(request.POST, instance=user_profile)
            if profile_form.is_valid():
                request.user.first_name = profile_form.cleaned_data.get('first_name', '').strip()
                request.user.last_name  = profile_form.cleaned_data.get('last_name', '').strip()
                request.user.save()
                profile_form.save()
                messages.success(request, 'Profile updated successfully.')
                return redirect('profile')
            else:
                messages.error(request, 'Please correct the errors below.')

        elif action == 'change_password':
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                password_form.save()
                # Keep user logged in after password change
                update_session_auth_hash(request, password_form.user)
                request.session.cycle_key()
                messages.success(request, 'Password changed successfully.')
                return redirect('profile')
            else:
                messages.error(request, 'Please correct the password errors below.')

    return render(request, 'mall/profile.html', {
        'orders':        orders,
        'profile':       user_profile,
        'profile_form':  profile_form,
        'password_form': password_form,
    })


# ─── Contact ──────────────────────────────────────────────────────────────────

def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == 'POST':
        if not check_rate_limit('contact', request, limit=3, window=3600):
            messages.error(request, 'Too many messages. Please try again later.')
            return redirect('contact')
        if form.is_valid():
            name    = form.cleaned_data['name']
            email   = form.cleaned_data['email']
            subject = form.cleaned_data['subject']
            msg     = form.cleaned_data['message']
            # Email to the store admin
            try:
                send_mail(
                    subject=f'Market Contact: {subject}',
                    message=f'From: {name} <{email}>\n\n{msg}',
                    from_email=django_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[django_settings.DEFAULT_FROM_EMAIL],
                    fail_silently=True,
                )
            except Exception:
                pass
            messages.success(request, 'Thank you! We will get back to you within 24 hours.')
            return redirect('contact')
    return render(request, 'mall/contact.html', {'form': form})


def terms(request):
    return render(request, 'mall/terms.html')

def privacy(request):
    return render(request, 'mall/privacy.html')


# ─── Branches Page ───────────────────────────────────────────────────────────

def branches(request):
    all_branches = Branch.objects.filter(is_active=True).order_by('region', 'name')
    grouped = {}
    for b in all_branches:
        grouped.setdefault(b.region, {'name': b.get_region_display(), 'branches': []})['branches'].append(b)
    return render(request, 'mall/branches.html', {'grouped': grouped, 'region_choices': REGION_CHOICES})


# ─── Nearest Branch API ───────────────────────────────────────────────────────

def _get_active_branches():
    """
    PERF-05 FIX: Cache the active branch list for 5 minutes.
    Branches rarely change so re-fetching them on every geolocation request
    is wasteful, especially with 50+ branches. Cache is invalidated naturally
    after TTL — no manual invalidation needed since branch changes are infrequent.
    """
    from django.core.cache import cache
    branches = cache.get('active_branches_with_gps')
    if branches is None:
        branches = list(Branch.objects.filter(is_active=True, latitude__isnull=False))
        cache.set('active_branches_with_gps', branches, timeout=300)  # 5 min TTL
    return branches


def _nearest_branch(lat: float, lng: float):
    """Return the nearest active Branch to given coordinates."""
    branches = _get_active_branches()
    return min(branches, key=lambda b: b.distance_to(lat, lng), default=None)


def _default_pickup_branch(profile=None):
    """
    Pick the branch a Branch-Pickup order should be assigned to.

    Customers no longer choose a branch by hand for pickup. After payment the
    order is routed to a branch officer for processing, and the customer is
    simply told where to collect. We therefore auto-assign a sensible default
    and let them optionally override it.

    Priority:
      1. The customer's saved nearest branch (from their profile)
      2. An active Main Branch — prefer one with GPS so directions work
      3. Any active branch with GPS coordinates
      4. Any active branch at all
    """
    if profile is not None:
        nb = getattr(profile, 'nearest_branch', None)
        if nb is not None and getattr(nb, 'is_active', False):
            return nb

    qs = Branch.objects.filter(is_active=True)
    for candidate in (
        qs.filter(branch_type='main', latitude__isnull=False),
        qs.filter(branch_type='main'),
        qs.filter(latitude__isnull=False),
        qs,
    ):
        b = candidate.order_by('region', 'name').first()
        if b is not None:
            return b
    return None


@require_POST
@login_required
def save_location(request):
    try:
        lat = float(request.POST.get('lat'))
        lng = float(request.POST.get('lng'))
        if not (-3.5 <= lng <= 1.5 and 4.5 <= lat <= 11.5):
            return JsonResponse({'ok': False, 'error': 'Coordinates outside Ghana'}, status=400)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Invalid coordinates'}, status=400)

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.latitude  = lat
    profile.longitude = lng
    branch = _nearest_branch(lat, lng)
    profile.nearest_branch = branch
    profile.save()

    return JsonResponse({
        'ok': True,
        'branch': {
            'id':       branch.id,
            'name':     branch.name,
            'city':     branch.city,
            'address':  branch.address,
            'phone':    branch.phone,
            'landmark': branch.landmark,
            'region':   branch.get_region_display(),
            'distance': round(branch.distance_to(lat, lng), 1),
        } if branch else None
    })


# ─── Review: mark helpful ─────────────────────────────────────────────────────

@require_POST
@login_required
def mark_review_helpful(request, review_id):
    review = get_object_or_404(Review, id=review_id, is_approved=True)
    if review.user == request.user:
        return JsonResponse({'ok': False, 'error': "You can't mark your own review as helpful."}, status=400)
    _, created = ReviewHelpful.objects.get_or_create(review=review, user=request.user)
    if created:
        review.helpful_count = review.helpful_votes.count()
        review.save(update_fields=['helpful_count'])
    return JsonResponse({'ok': True, 'helpful_count': review.helpful_count, 'already_voted': not created})


# ─── Post-purchase review prompt (from My Orders page) ───────────────────────

@login_required
def order_review(request, order_id):
    """
    Shows a review form for all delivered, un-reviewed items in a specific order.
    Accessed via a 'Review Your Order' button on the My Orders page.
    """
    order = get_object_or_404(Order, id=order_id, user=request.user, status='delivered')
    items = order.items.select_related('product').all()

    # Build list of items that haven't been reviewed yet
    pending_items = []
    for item in items:
        already_reviewed = Review.objects.filter(user=request.user, product=item.product).exists()
        if not already_reviewed:
            pending_items.append({
                'item':    item,
                'product': item.product,
                'form':    ReviewForm(prefix=f'item_{item.id}'),
            })

    if request.method == 'POST':
        submitted = 0
        errors    = []
        for entry in pending_items:
            item    = entry['item']
            form    = ReviewForm(request.POST, prefix=f'item_{item.id}')
            if form.is_valid():
                r = form.save(commit=False)
                r.product              = item.product
                r.user                 = request.user
                r.rating               = int(form.cleaned_data['rating'])
                r.order_item           = item
                r.is_verified_purchase = True
                r.save()
                submitted += 1
            else:
                errors.append(item.product.name)

        if submitted:
            messages.success(request, f'✅ Thank you! {submitted} review{"s" if submitted > 1 else ""} submitted.')
        if errors:
            messages.warning(request, f'Some reviews had errors: {", ".join(errors)}')
        if submitted and not errors:
            return redirect('my_orders')

        # Re-build form list for any that had errors
        pending_items = []
        for item in items:
            already_reviewed = Review.objects.filter(user=request.user, product=item.product).exists()
            if not already_reviewed:
                form = ReviewForm(request.POST, prefix=f'item_{item.id}')
                form.is_valid()  # trigger validation so errors show
                pending_items.append({'item': item, 'product': item.product, 'form': form})

    return render(request, 'mall/order_review.html', {
        'order':         order,
        'pending_items': pending_items,
    })


# ─── FEAT-01: Wishlist ────────────────────────────────────────────────────────

@login_required
def wishlist_view(request):
    items = WishlistItem.objects.filter(user=request.user).select_related('product__category')
    return render(request, 'mall/wishlist.html', {'wishlist_items': items})


@require_POST
@login_required
def wishlist_toggle(request, product_id):
    product = get_object_or_404(Product, id=product_id, available=True)
    item, created = WishlistItem.objects.get_or_create(user=request.user, product=product)
    if not created:
        item.delete()
    return JsonResponse({'ok': True, 'wishlisted': created, 'product_id': product_id})


@require_POST
@login_required
def wishlist_add_all_to_cart(request):
    items = WishlistItem.objects.filter(user=request.user).select_related('product')
    cart  = get_cart(request)
    added = 0
    for wi in items:
        p = wi.product
        if p.available and p.stock > 0:
            key = str(p.id)
            cart[key] = min(cart.get(key, 0) + 1, p.stock, 99)
            added += 1
    save_cart(request, cart)
    if added:
        messages.success(request, f'{added} item(s) added to your cart.')
    else:
        messages.warning(request, 'No available items to add.')
    return redirect('cart')


# ─── Wishlist count API ──────────────────────────────────────────────────────

def wishlist_count(request):
    """Returns number of items in user's watchlist. Returns 0 for unauthenticated users."""
    if not request.user.is_authenticated:
        return JsonResponse({'count': 0})
    count = WishlistItem.objects.filter(user=request.user).count()
    return JsonResponse({'count': count})


# ─── FEAT-02: Order Search & Filter ──────────────────────────────────────────

@login_required
def my_orders(request):
    from django.db.models import Q, Exists, OuterRef, Prefetch
    # PERF: prefetch related fields the new Jumia-style template touches
    # per item (product image, category, review) so we don't N+1 on a
    # page with many orders × many items.
    items_qs = OrderItem.objects.select_related('product', 'product__category').prefetch_related('review')
    all_orders = Order.objects.filter(user=request.user).select_related('branch').prefetch_related(
        Prefetch('items', queryset=items_qs)
    ).annotate(
        feedback_submitted=Exists(
            OrderFeedback.objects.filter(order=OuterRef('pk'))
        )
    ).order_by('-created')

    # FEAT-02: filter by status, date range, or product name
    status_filter = request.GET.get('status', '')
    date_from     = request.GET.get('date_from', '')
    date_to       = request.GET.get('date_to', '')
    product_q     = request.GET.get('product', '')

    if status_filter:
        all_orders = all_orders.filter(status=status_filter)
    if date_from:
        try:
            all_orders = all_orders.filter(created__date__gte=date_from)
        except Exception:
            pass
    if date_to:
        try:
            all_orders = all_orders.filter(created__date__lte=date_to)
        except Exception:
            pass
    if product_q:
        all_orders = all_orders.filter(items__product__name__icontains=product_q).distinct()

    active_count    = Order.objects.filter(user=request.user, status__in=['pending', 'processing', 'shipped']).count()
    delivered_count = Order.objects.filter(user=request.user, status='delivered').count()

    paginator = Paginator(all_orders, 10)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/my_orders.html', {
        'orders':          page_obj,
        'page_obj':        page_obj,
        'active_count':    active_count,
        'delivered_count': delivered_count,
        'status_filter':   status_filter,
        'date_from':       date_from,
        'date_to':         date_to,
        'product_q':       product_q,
        'status_choices':  Order.STATUS_CHOICES,
    })


@login_required
def order_history(request):
    """
    Legacy URL — redirected to /my-orders/ which now provides the full
    order list with product images, item details, and tracking. The two
    pages used to do similar things; consolidating to one Jumia-style
    page is cleaner and reduces template maintenance.

    Kept as a 301 redirect so any old bookmarks or external links
    (sitemap, marketing emails, etc.) continue to land on the right page.
    """
    from django.shortcuts import redirect
    return redirect('my_orders', permanent=True)


# ─── FEAT-05: Order Cancellation ─────────────────────────────────────────────

@require_POST
@login_required
def cancel_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    if order.status != 'pending':
        messages.error(request, 'Only pending orders can be cancelled.')
        return redirect('my_orders')

    # FIX-CANCEL-ATOMIC: Wrap the entire cancel + stock-restore in a single
    # transaction so a mid-loop crash cannot leave inventory in a partial state.
    from django.db import transaction
    with transaction.atomic():
        for item in order.items.select_related('product').all():
            Product.objects.filter(pk=item.product.pk).update(stock=F('stock') + item.quantity)
        order.status = 'cancelled'
        order.save(update_fields=['status'])

    # Notify admin
    _notify_admins_new_order_cancelled(order)
    messages.success(request, f'Order {order.order_number} has been cancelled. Stock has been restored.')
    return redirect('my_orders')


def _notify_admins_new_order_cancelled(order):
    from django.contrib.auth.models import User as _User
    staff_users = _User.objects.filter(is_staff=True, is_active=True)
    for staff in staff_users:
        _notify(
            user=staff,
            notif_type='order_cancel',
            title=f'Order {order.order_number} Cancelled by Customer',
            message=f'{order.full_name} cancelled Order {order.order_number} (GH₵ {order.total_price}). Stock has been restored.',
            link=f'/panel/orders/{order.id}/',
        )


# ─── FEAT-07: Promo Code AJAX validation ─────────────────────────────────────

@require_POST
@login_required
def apply_promo_code(request):
    """AJAX endpoint — validate a promo code and return the discount amount."""
    if not check_rate_limit('promo_code', request, limit=20, window=300):
        return JsonResponse({'ok': False, 'error': 'Too many attempts. Please wait a few minutes.'}, status=429)
    code_str = request.POST.get('code', '').strip().upper()
    try:
        subtotal = Decimal(request.POST.get('subtotal', '0'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid subtotal.'}, status=400)

    try:
        promo = PromoCode.objects.get(code=code_str)
    except PromoCode.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Promo code not found.'})

    valid, err = promo.is_valid()
    if not valid:
        return JsonResponse({'ok': False, 'error': err})

    if subtotal < promo.min_order_value:
        return JsonResponse({
            'ok': False,
            'error': f'This code requires a minimum order of GH₵ {promo.min_order_value}.'
        })

    discount = promo.calculate_discount(subtotal)
    request.session['promo_code'] = code_str
    return JsonResponse({
        'ok':            True,
        'code':          promo.code,
        'discount':      float(discount),
        'discount_type': promo.discount_type,
        'discount_value': float(promo.discount_value),
        'message':       f'Code "{promo.code}" applied — you save GH₵ {discount}!',
    })


# ─── FEAT-NOTIF: Notifications ────────────────────────────────────────────────

@login_required
def notifications_view(request):
    notifs = Notification.objects.filter(user=request.user).order_by('-created')
    unread = notifs.filter(is_read=False).count()
    paginator = Paginator(notifs, 20)
    page_obj  = paginator.get_page(request.GET.get('page'))
    return render(request, 'mall/notifications.html', {
        'notifications': page_obj,
        'page_obj':      page_obj,
        'unread_count':  unread,
    })


@require_POST
@login_required
def mark_notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required
def notifications_count(request):
    count = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({'unread': count})



# ─── FEAT: Order Feedback ─────────────────────────────────────────────────────

@login_required
def order_feedback(request, order_id):
    """
    Customer leaves feedback on a completed order.

    Accepts both 'delivered' (rider/officer marked done) and 'confirmed'
    (customer themselves confirmed receipt) — both mean the order arrived
    successfully and feedback is appropriate.

    Returns helpful 404s instead of bare ones so the customer knows whether
    the issue is "you don't own this order" vs "this order isn't ready
    for feedback yet" vs "you already left feedback".
    """
    # Look up the order WITHOUT the status filter so we can give a more
    # informative error if the status is wrong.
    try:
        order = Order.objects.get(id=order_id, user=request.user)
    except Order.DoesNotExist:
        # Either the order doesn't exist or it doesn't belong to this user.
        # We don't differentiate — telling someone "this order exists but
        # isn't yours" leaks information.
        messages.error(
            request,
            "We couldn't find that order on your account. "
            "Make sure you're signed in with the email used to place the order."
        )
        return redirect('my_orders')

    # Only one feedback per order
    if hasattr(order, 'feedback'):
        messages.info(request, 'You have already submitted feedback for this order. Thank you!')
        return redirect('my_orders')

    # Status check
    if order.status not in ('delivered', 'confirmed'):
        messages.warning(
            request,
            f"You can only leave feedback after your order has been delivered. "
            f"This order is currently '{order.get_status_display()}'."
        )
        return redirect('my_orders')

    form = OrderFeedbackForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        fb = form.save(commit=False)
        fb.order = order
        fb.user  = request.user
        fb.save()
        messages.success(request, '🙏 Thank you for your feedback! It helps us improve.')
        return redirect('my_orders')

    return render(request, 'mall/order_feedback.html', {'order': order, 'form': form})


# ─── FEAT: AI — Chat Assistant ────────────────────────────────────────────────

@login_required
def ai_chat(request):
    """
    Renders the AI chat widget page. The JS widget fetches the system-prompt
    context from ai_chat_context() — a dedicated JSON endpoint — rather than
    having it embedded in the rendered HTML. This prevents order history
    (IDs, totals, statuses) from being visible in the page source.
    """
    categories = Category.objects.all()
    return render(request, 'mall/ai_chat.html', {
        'categories_list': ', '.join(c.name for c in categories),
    })


@login_required
def ai_chat_context(request):
    """
    FIX-AICHAT: Returns the user's recent order context as JSON so the
    AI chat widget can build its system prompt client-side from a fetch()
    call rather than from an inline template variable.
    The endpoint is @login_required, so only the authenticated user can
    retrieve their own order summary. The data never lands in HTML source.
    """
    recent_orders = Order.objects.filter(user=request.user).order_by('-created')[:5]
    orders_lines  = [
        f"{o.order_number}: {o.status} on {o.created.strftime('%b %d, %Y')} — GH₵{o.total_price}"
        for o in recent_orders
    ]
    return JsonResponse({
        'orders_context': '; '.join(orders_lines) if orders_lines else 'No orders yet',
    })


# ─── FEAT: AI — Product Recommendations ──────────────────────────────────────

@login_required
def ai_recommendations(request):
    """
    Returns AI-generated product recommendations as JSON.
    Sends browsing/order history context to Claude and returns suggestions.
    """
    import urllib.request, urllib.error

    # Build context: recent orders + wishlist + categories
    recent_order_items = OrderItem.objects.filter(
        order__user=request.user
    ).select_related('product__category').order_by('-order__created')[:10]

    wishlist_products = WishlistItem.objects.filter(
        user=request.user
    ).select_related('product__category')[:10]

    all_categories = list(Category.objects.values_list('name', flat=True))

    purchased = [f"{oi.product.name} ({oi.product.category.name})" for oi in recent_order_items]
    wishlisted = [f"{wi.product.name} ({wi.product.category.name})" for wi in wishlist_products]

    # Available products sample (up to 30 for context window)
    available = list(
        Product.objects.filter(available=True, stock__gt=0)
        .select_related('category')
        .order_by('?')[:30]
        .values('name', 'price', 'slug', 'category__name')
    )
    available_text = '\n'.join(
        f"- {p['name']} | GH₵{p['price']} | {p['category__name']} | slug:{p['slug']}"
        for p in available
    )

    prompt = f"""You are a product recommendation engine for Market, a Ghanaian e-commerce store.

Customer context:
- Recently purchased: {', '.join(purchased) or 'nothing yet'}
- Wishlisted: {', '.join(wishlisted) or 'nothing'}
- Available categories: {', '.join(all_categories)}

Available products right now:
{available_text}

Based on the customer's purchase history and wishlist, recommend exactly 4 products from the list above.
Respond ONLY with a JSON array of objects, no markdown, no explanation. Each object must have:
- "name": product name
- "slug": product slug (use exactly as given above)
- "price": price as a number
- "category": category name
- "reason": one short sentence (max 12 words) explaining why you recommend this

Example: [{{"name":"X","slug":"x-slug","price":49.99,"category":"Electronics","reason":"Pairs well with your recent phone purchase."}}]"""

    try:
        from django.conf import settings as django_settings
        api_key = django_settings.ANTHROPIC_API_KEY
        if not api_key:
            return JsonResponse({'ok': False, 'error': 'AI recommendations are not configured.'}, status=200)

        import json as _json
        payload = _json.dumps({
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': 500,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
            text = data['content'][0]['text'].strip()
            # Strip any accidental markdown fences
            if text.startswith('```'):
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            recs = _json.loads(text)
            # Enrich with actual product data for accurate prices/availability
            slugs = [r.get('slug') for r in recs if r.get('slug')]
            products_map = {
                p.slug: p for p in Product.objects.filter(slug__in=slugs, available=True)
            }
            enriched = []
            for r in recs:
                p = products_map.get(r.get('slug'))
                if p:
                    enriched.append({
                        'name':     p.name,
                        'slug':     p.slug,
                        'price':    float(p.price),
                        'category': p.category.name,
                        'image':    p.image.url if p.image else None,
                        'reason':   r.get('reason', ''),
                        'url':      f'/products/{p.slug}/',
                    })
            return JsonResponse({'ok': True, 'recommendations': enriched})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=200)


# ─── FEAT: AI — Review Summariser (AJAX) ─────────────────────────────────────

def ai_review_summary(request, product_slug):
    """
    Returns an AI-generated summary of all approved reviews for a product.
    Called via AJAX from the product detail page.
    """
    import urllib.request, urllib.error, json as _json

    product = get_object_or_404(Product, slug=product_slug, available=True)
    reviews = product.reviews.filter(is_approved=True).order_by('-created')[:50]

    if reviews.count() < 3:
        return JsonResponse({'ok': False, 'error': 'Not enough reviews to summarise yet.'})

    review_text = '\n'.join(
        f"[{r.rating}★] {r.title + ': ' if r.title else ''}{r.comment[:300]}"
        for r in reviews
    )

    prompt = f"""Summarise these customer reviews for "{product.name}" in 3–4 sentences.
Be balanced — mention both positives and any common concerns.
Write directly to a potential buyer in plain English. Do not use bullet points.

Reviews:
{review_text}"""

    try:
        from django.conf import settings as django_settings
        api_key = django_settings.ANTHROPIC_API_KEY
        if not api_key:
            return JsonResponse({'ok': False, 'error': 'Review summaries are not configured.'}, status=200)

        payload = _json.dumps({
            'model': 'claude-sonnet-4-20250514',
            'max_tokens': 300,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
            summary = data['content'][0]['text'].strip()
            return JsonResponse({'ok': True, 'summary': summary, 'count': reviews.count()})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': 'Could not generate summary right now.'}, status=200)



# ═══════════════════════════════════════════════════════════════════════════════
#  RIDER DELIVERY SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise_phone(phone: str) -> str:
    """
    Normalise a Ghana phone number to E.164 format (+233XXXXXXXXX).
    Handles: 0244123456, +233244123456, 233244123456, 244123456
    """
    import re
    digits = re.sub(r'[^\d]', '', phone.strip())
    if digits.startswith('233'):
        return '+' + digits
    if digits.startswith('0') and len(digits) == 10:
        return '+233' + digits[1:]
    if len(digits) == 9:
        return '+233' + digits
    # Already has country code without +
    return '+' + digits


def _send_whatsapp(phone: str, message: str):
    """
    Send a WhatsApp message via Twilio WhatsApp API.
    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_WHATSAPP_FROM in .env.
    TWILIO_WHATSAPP_FROM is your Twilio sandbox/approved number, e.g. +14155238886

    Falls back to console log when credentials are not set (local dev).
    Sign up free at https://www.twilio.com — WhatsApp sandbox is free to test.
    """
    sid   = getattr(django_settings, 'TWILIO_ACCOUNT_SID', '')
    token = getattr(django_settings, 'TWILIO_AUTH_TOKEN', '')
    from_  = getattr(django_settings, 'TWILIO_WHATSAPP_FROM', '')

    if not sid or not token or not from_:
        print(f'[WhatsApp — not configured] To {phone}: {message}')
        return

    try:
        to_num   = _normalise_phone(phone)
        wa_from  = f'whatsapp:{from_}' if not from_.startswith('whatsapp:') else from_
        wa_to    = f'whatsapp:{to_num}'

        # Twilio Messages API — plain HTTP, no SDK required
        import base64
        credentials = base64.b64encode(f'{sid}:{token}'.encode()).decode()
        payload = urllib.parse.urlencode({
            'From': wa_from,
            'To':   wa_to,
            'Body': message,
        }).encode()
        req = urllib.request.Request(
            f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
            data=payload,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type':  'application/x-www-form-urlencoded',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get('status') not in ('queued', 'sent', 'delivered'):
                print(f'[WhatsApp warning] status={result.get("status")} sid={result.get("sid")}')
    except urllib.error.HTTPError as e:
        print(f'[WhatsApp error] HTTP {e.code}: {e.read().decode()[:200]}')
    except Exception as e:
        print(f'[WhatsApp error] {e}')


# Keep _send_sms as an alias so existing call sites don't need changing
_send_sms = _send_whatsapp


def _notify_rider_dispatched(order, rider):
    """
    Notify admins + customer when rider is assigned and dispatched.

    Uses the unified notify() dispatcher so each recipient gets the message
    on every channel they have available (in-app for everyone, plus WA + SMS
    to the customer's phone).
    """
    from .notify import notify_admins, notify

    # In-app for all staff (admins use the panel — no WA/SMS needed)
    notify_admins(
        notif_type='rider_dispatched',
        title=f'🛵 Rider Assigned — Order {order.order_number}',
        message=f'{rider.rider_name} dispatched to deliver Order {order.order_number} to {order.full_name}.',
        link=f'/panel/orders/{order.id}/',
    )

    # Customer — in-app + WA + SMS in a single call.
    # Phone resolution: prefer the order's phone (handles guest checkouts
    # where order.user might not have a profile phone).
    if order.user_id:
        notify(
            order.user,
            notif_type='rider_dispatched',
            title='🛵 Your order is on its way!',
            message=(
                f'A rider ({rider.rider_name}) has been dispatched with your '
                f'Order {order.order_number}. Expected delivery soon.'
            ),
            link='/my-orders/',
            whatsapp_text=(
                f'Honey Cave Market — Your order {order.order_number} is on the way! '
                f'Rider: {rider.rider_name} ({rider.rider_phone}). '
                f'You\'ll receive your delivery code shortly to show on arrival.'
            ),
            sms_text=(
                f'Honey Cave: Order {order.order_number} dispatched. '
                f'Rider {rider.rider_name} ({rider.rider_phone}) is on the way. '
                f'You\'ll get a delivery code shortly to show them.'
            ),
            phone_override=order.phone or '',
        )
    else:
        # Guest customer — no User account, but still send WA/SMS to their phone
        from .notify import notify_phone
        notify_phone(
            phone=order.phone or '',
            whatsapp_text=(
                f'Honey Cave Market — Your order {order.order_number} is on the way! '
                f'Rider: {rider.rider_name} ({rider.rider_phone}).'
            ),
            sms_text=(
                f'Honey Cave: Order {order.order_number} dispatched. '
                f'Rider {rider.rider_name} on the way.'
            ),
        )

    # Email: customer
    try:
        subject = f'Honey Cave Market — Your Order {order.order_number} is On Its Way! 🛵'
        body_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
          <div style="background:#c9a84c;padding:28px 32px;border-radius:12px 12px 0 0;text-align:center;">
            <h1 style="color:#fff;margin:0;font-size:22px;">🛵 Order {order.order_number} Dispatched</h1>
          </div>
          <div style="background:#fff;padding:28px 32px;border-radius:0 0 12px 12px;border:1px solid #e8dcc8;">
            <p style="color:#3a2e22;">Hi <strong>{order.full_name}</strong>,</p>
            <p style="color:#5a5047;">Great news — your order is on its way!</p>
            <div style="background:#fdf5e0;border-radius:8px;padding:16px;margin:16px 0;">
              <p style="margin:0;font-size:14px;color:#7a5c00;">
                🚴 <strong>Rider:</strong> {rider.rider_name}<br>
                📞 <strong>Rider Phone:</strong> {rider.rider_phone}<br>
                📦 <strong>Order:</strong> {order.order_number}<br>
                📍 <strong>Delivery Address:</strong> {order.delivery_address or order.address}
              </p>
            </div>
            <p style="color:#5a5047;font-size:14px;">
              Once delivered, you'll receive another notification to confirm receipt.
            </p>
          </div>
        </div>"""
        msg = EmailMultiAlternatives(subject, '', django_settings.DEFAULT_FROM_EMAIL, [order.email])
        msg.attach_alternative(body_html, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass

    # SMS: customer
    _send_sms(
        order.phone,
        f'*Honey Cave Market* — Your order is on its way! 🛵\n\n'
        f'Order: {order.order_number}\n'
        f'Rider: {rider.rider_name}\n'
        f'Rider phone: {rider.rider_phone}\n\n'
        f'Your delivery is en route. You can call the rider directly if needed.'
    )


def _notify_delivery_done(order, rider):
    """Notify admins + customer when rider marks order as delivered."""
    from .notify import notify_admins, notify, notify_phone

    # Admins (in-app only)
    notify_admins(
        notif_type='delivery_done',
        title=f'✅ Delivered — Order {order.order_number}',
        message=f'Rider {rider.rider_name} marked Order {order.order_number} as delivered to {order.full_name}.',
        link=f'/panel/orders/{order.id}/',
    )

    # Customer (in-app + WA + SMS) — make sure they know to confirm
    confirm_link = f'/orders/{order.id}/confirm-delivery/'
    confirm_url  = f'{django_settings.SITE_URL}{confirm_link}'
    wa_text = (
        f'Honey Cave Market — Order {order.order_number} marked delivered by rider {rider.rider_name}. '
        f'Please confirm receipt or report a problem: {confirm_url}'
    )
    sms_text = (
        f'Honey Cave: Order {order.order_number} marked delivered. '
        f'Confirm receipt: {confirm_url}'
    )
    if order.user_id:
        notify(
            order.user,
            notif_type='delivery_done',
            title=f'📦 Order {order.order_number} Delivered!',
            message='Your order has been marked as delivered. Please confirm receipt or report a problem.',
            link=confirm_link,
            whatsapp_text=wa_text,
            sms_text=sms_text,
            phone_override=order.phone or '',
        )
    elif order.phone:
        notify_phone(phone=order.phone, whatsapp_text=wa_text, sms_text=sms_text)

    # Email: customer
    try:
        subject = f'Honey Cave Market — Order {order.order_number} Delivered! Please Confirm ✅'
        confirm_url = f'{django_settings.SITE_URL}{confirm_link}'
        body_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
          <div style="background:#2e7d32;padding:28px 32px;border-radius:12px 12px 0 0;text-align:center;">
            <h1 style="color:#fff;margin:0;font-size:22px;">✅ Order {order.order_number} Delivered!</h1>
          </div>
          <div style="background:#fff;padding:28px 32px;border-radius:0 0 12px 12px;border:1px solid #e8dcc8;">
            <p style="color:#3a2e22;">Hi <strong>{order.full_name}</strong>,</p>
            <p style="color:#5a5047;">
              Rider <strong>{rider.rider_name}</strong> has marked your order as delivered.
              Did you receive it?
            </p>
            <div style="text-align:center;margin:24px 0;display:flex;gap:12px;justify-content:center;">
              <a href="{confirm_url}?action=confirm"
                 style="background:#2e7d32;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;">
                ✅ Yes, I got it
              </a>
              <a href="{confirm_url}?action=problem"
                 style="background:#c62828;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;">
                ⚠️ Report Problem
              </a>
            </div>
            <p style="color:#999;font-size:12px;text-align:center;">
              This confirmation helps us ensure every delivery is successful.
            </p>
          </div>
        </div>"""
        msg = EmailMultiAlternatives(subject, '', django_settings.DEFAULT_FROM_EMAIL, [order.email])
        msg.attach_alternative(body_html, 'text/html')
        msg.send(fail_silently=True)
    except Exception:
        pass

    # SMS: customer
    _send_sms(
        order.phone,
        f'*Honey Cave Market* — Your order has been delivered! ✅\n\n'
        f'Order: {order.order_number}\n\n'
        f'Please confirm you received it:\n'
        f'{django_settings.SITE_URL}{confirm_link}\n\n'
        f'If you have any issues, reply here or call us.'
    )

    # SMS: admin summary (first staff phone if available)
    try:
        from django.contrib.auth.models import User as _User
        admin_user = _User.objects.filter(is_staff=True, is_active=True).first()
        if admin_user:
            profile = getattr(admin_user, 'profile', None)
            admin_phone = getattr(profile, 'phone', None) if profile else None
            if admin_phone:
                _send_sms(
                    admin_phone,
                    f'*HCM Admin* — Order {order.order_number} delivered\n'
                    f'Rider: {rider.rider_name}\n'
                    f'Customer: {order.full_name}\n'
                    f'View: {django_settings.SITE_URL}/panel/orders/{order.id}/'
                )
    except Exception:
        pass


def _notify_delivery_confirmed(order):
    """Notify admin when customer confirms receipt."""
    from django.contrib.auth.models import User as _User
    staff_users = _User.objects.filter(is_staff=True, is_active=True)
    for staff in staff_users:
        _notify(
            user=staff,
            notif_type='delivery_confirm',
            title=f'🎉 Customer Confirmed — Order {order.order_number}',
            message=f'{order.full_name} confirmed receipt of Order {order.order_number}.',
            link=f'/panel/orders/{order.id}/',
        )


# ─── Rider portal (token-authenticated, no login required) ───────────────────

@csrf_exempt   # No session cookie — Django CSRF middleware cannot apply here.
               # FIX-RIDER-CSRF: We compensate by re-validating the URL token on
               # every POST. The template echoes the token in a hidden field
               # (name="rider_token"). A forged cross-origin POST that omits or
               # mismatches the field is rejected via constant_time_compare.
def rider_delivery_portal(request, token):
    """
    The page a rider opens on their phone.
    Secured by a unique token — no account needed.

    Three actions, depending on order state:
      1. Verify Code 2 (keeper_to_rider) — proves rider received from fulfillment officer
      2. Show Code 3 (rider_to_customer) — what the customer must enter
      3. Mark delivered — final step (only after customer verifies Code 3)
    """
    rider = get_object_or_404(RiderDelivery, token=token)
    order = rider.order
    submitted = False
    error = None
    handoff_status = None  # 'ok' | 'wrong' | 'locked' | 'expired' | 'not_found'
    handoff_remaining = 0

    if request.method == 'POST':
        # Re-validate that the token in the POST body matches the URL token.
        posted_token = request.POST.get('rider_token', '')
        if not constant_time_compare(posted_token, token):
            error = 'Invalid request. Please open your delivery link again.'
        else:
            action = request.POST.get('action', '')

            if action == 'verify_keeper_code':
                # Rider entering the code shown to them by the fulfillment officer
                from . import handoff as _handoff_svc
                entered = request.POST.get('code', '').strip()
                handoff_status, _, handoff_remaining = _handoff_svc.verify_code(
                    order, 'officer_to_rider', entered, used_by_user=None,
                )
                if handoff_status == 'ok':
                    # Auto-advance issued the customer code already (in handoff service)
                    pass

            elif action == 'verify_customer_code':
                # FLIPPED-FLOW: Customer's phone shows a code; rider asks for it
                # and enters it here. Verifying this code closes the chain and
                # automatically marks the order delivered (handled below).
                from . import handoff as _handoff_svc
                entered = request.POST.get('code', '').strip()
                handoff_status, _, handoff_remaining = _handoff_svc.verify_code(
                    order, 'rider_to_customer', entered, used_by_user=None,
                )
                if handoff_status == 'ok':
                    # Mark this delivery row as delivered too — keeps the
                    # RiderDelivery record's delivered_at/rider_note in sync
                    # with the chain. advance_after_verify() in handoff.py
                    # has already set order.status = 'delivered'.
                    if not rider.is_delivered:
                        from django.utils import timezone
                        rider.delivered_at = timezone.now()
                        rider.rider_note = sanitize_text(request.POST.get('rider_note', ''), 300)
                        rider.save()
                        _notify_delivery_done(order, rider)
                    submitted = True

    # Build handoff context for the template
    keeper_code = order.handoff_codes.filter(stage='officer_to_rider').order_by('-created_at').first()
    customer_code = order.handoff_codes.filter(stage='rider_to_customer').order_by('-created_at').first()

    return render(request, 'mall/rider_portal.html', {
        'rider': rider,
        'order': order,
        'submitted': submitted,
        'error': error,
        # Handoff data
        'keeper_code':       keeper_code,
        'customer_code':     customer_code,
        'handoff_status':    handoff_status,
        'handoff_remaining': handoff_remaining,
    })
# ─── Customer delivery confirmation ──────────────────────────────────────────

@login_required
def confirm_delivery(request, order_id):
    """
    Customer-facing handoff page (FLIPPED-FLOW).

    The customer no longer enters a code here — instead, this page DISPLAYS
    the rider_to_customer (or keeper_to_customer for pickup) code that was
    auto-issued when the previous chain stage was verified. The customer
    shows it to the rider/fulfillment officer, who enters it on THEIR portal to
    close the chain.

    The page also keeps an "I have a problem" escape hatch so a customer
    who never receives the package (rider no-show, wrong items, etc.) can
    flag it to admin without entering any code.
    """
    order = get_object_or_404(
        Order.objects.prefetch_related('handoff_codes', 'items__product'),
        id=order_id, user=request.user,
    )
    rider = getattr(order, 'rider_delivery', None)

    # Pick the right code based on fulfillment type
    target_stage = (
        'officer_to_customer' if order.fulfillment_type == 'pickup'
        else 'rider_to_customer'
    )
    customer_code = (
        order.handoff_codes.filter(stage=target_stage)
        .order_by('-created_at').first()
    )

    problem_reported = False

    # POST is now ONLY for "I have a problem" — code entry is gone from this page.
    if request.method == 'POST' and request.POST.get('action') == 'problem':
        if not check_rate_limit('delivery_problem', request, limit=3, window=300):
            messages.error(request, 'Too many submissions. Please slow down.')
            return redirect('confirm_delivery', order_id=order.id)
        from django.contrib.auth.models import User as _User
        for staff in _User.objects.filter(is_staff=True, is_active=True):
            _notify(
                user=staff,
                notif_type='delivery_done',
                title=f'⚠️ Delivery Problem — Order {order.order_number}',
                message=(
                    f'{order.full_name} reported a problem with order '
                    f'{order.order_number}. Please follow up at {order.phone}.'
                ),
                link=f'/panel/orders/{order.id}/',
            )
        problem_reported = True

    return render(request, 'mall/confirm_delivery.html', {
        'order':            order,
        'rider':            rider,
        'customer_code':    customer_code,
        'target_stage':     target_stage,
        'problem_reported': problem_reported,
        'is_complete':      order.status in ('delivered', 'confirmed'),
    })


# ─── Promotions click tracker ────────────────────────────────────────────────

def promotion_click(request, pk):
    """
    Records a click on an internal promotion banner and redirects to the
    promotion's link_url. If the promo is gone or inactive, fall back to
    the homepage so the user never sees a broken link.
    """
    try:
        promo = Promotion.objects.get(pk=pk)
    except Promotion.DoesNotExist:
        return redirect('home')
    # Use an F() update so concurrent clicks don't lose counts
    Promotion.objects.filter(pk=pk).update(clicks=F('clicks') + 1)
    target = (promo.link_url or '').strip()
    # Safety: only allow same-site paths or validated external URLs.
    # safe_redirect_url falls back to home if the URL is off-site or malformed.
    if target:
        url = safe_redirect_url(target, request, fallback='/')
        return redirect(url)
    return redirect('home')
