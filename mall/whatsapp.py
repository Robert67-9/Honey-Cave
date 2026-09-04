"""
WhatsApp notifications via Meta Cloud API or Tiliow.

This module exposes two functions:
    send_template(to, template_name, variables)   — send an approved template
    send_plain(to, text)                          — send free-form text
                                                    (only works within the 24-hour
                                                    customer-service window)

Both are safe to call even when WhatsApp is not configured — they return
False and do nothing instead of raising. That means wiring these calls into
the order flow never breaks checkout, even if credentials are missing.

Prerequisites before messages actually send:
  1. Meta Business verification + WhatsApp Business Account set up
  2. At least one phone number added in Meta dashboard → WhatsApp → API Setup
  3. Template messages created and APPROVED in Meta Business → Message Templates
  4. Admin fills in wa_phone_number_id, wa_access_token, wa_enabled=True
     in Panel → Site Settings

If Tiliow is configured using environment variables, plain WhatsApp text
messages are sent through Tiliow instead of Meta Cloud.

Template message format:
  Templates have numbered variables {{1}}, {{2}}, {{3}}, etc. When calling
  send_template(), pass a list that maps positionally to those variables.

Example template you would create in Meta Business Manager:
  Name:     order_confirmation
  Category: UTILITY
  Body:     "Hi {{1}}, your Honey Cave Market order {{2}} has been received.
             Total: GH₵ {{3}}. We'll message you again when it's ready.
             Thank you for shopping with us!"

Then call:
  send_template('233591784205', 'order_confirmation',
                ['Ama', '#HCM000123', '150.00'])
"""
import json
import logging
import os
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)

META_API_BASE = 'https://graph.facebook.com/v20.0'


def _normalize_phone(raw):
    """
    Meta expects E.164 digits-only (no '+' sign, no spaces, no dashes).
    Accepts '+233 59 178 4205', '0591784205', '233591784205' and returns
    '233591784205'. For Ghanaian numbers starting with '0', prepends '233'.
    """
    if not raw:
        return ''
    digits = ''.join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return ''
    # Local Ghanaian format (0591784205) → 233591784205
    if digits.startswith('0') and len(digits) == 10:
        digits = '233' + digits[1:]
    return digits


def _load_config():
    """
    Pull live config from SiteSettings. Returns (enabled, phone_number_id,
    access_token) or (False, None, None) when WhatsApp is off or misconfigured.
    Lazy-imported here to avoid circular imports at module-load time.
    """
    try:
        from .models import SiteSettings
        s = SiteSettings.load()
    except Exception as e:
        logger.warning('WhatsApp: could not load SiteSettings: %s', e)
        return (False, None, None, None)
    if not s.wa_enabled:
        return (False, None, None, s)
    phone_id = (s.wa_phone_number_id or '').strip()
    token    = (s.wa_access_token or '').strip()
    if not phone_id or not token:
        logger.warning('WhatsApp: enabled but phone_number_id or access_token missing.')
        return (False, None, None, s)
    return (True, phone_id, token, s)


def _load_tiliow_config():
    """
    Load Tiliow configuration.

    Priority:
      1. SiteSettings (admin-managed, the supported way) — when tiliow_enabled
         is on and an API key is present.
      2. Environment variables (legacy / ops override).

    Returns (api_key, api_url, sender_id) or (None, None, None) when off.
    """
    # 1. Admin-managed Site Settings
    try:
        from .models import SiteSettings
        s = SiteSettings.load()
        if getattr(s, 'tiliow_enabled', False) and (s.tiliow_api_key or '').strip():
            return (
                (s.tiliow_api_key or '').strip(),
                (s.tiliow_api_url or 'https://api.tiliow.com/v1/messages').strip(),
                (s.tiliow_sender_id or '').strip(),
            )
    except Exception as e:
        logger.debug('Tiliow: SiteSettings unavailable, trying env vars: %s', e)

    # 2. Environment variables
    api_key = (os.environ.get('TILIOW_API_KEY') or
               os.environ.get('TILIOW_BEARER_TOKEN') or
               os.environ.get('TILIOW_TOKEN') or '').strip()
    if not api_key:
        return None, None, None
    api_url = os.environ.get('TILIOW_API_URL', 'https://api.tiliow.com/v1/messages').strip()
    sender_id = (os.environ.get('TILIOW_SENDER_ID') or
                 os.environ.get('TILIOW_WHATSAPP_FROM') or
                 '').strip()
    return api_key, api_url, sender_id


def _format_tiliow_template(template_name, variables):
    text = template_name.replace('_', ' ').capitalize() if template_name else ''
    if variables:
        vars_text = ', '.join(str(v) for v in variables if v is not None)
        if vars_text:
            text = f'{text}: {vars_text}'
    return text[:4000]


def _post_tiliow(payload, api_url, api_key):
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(api_url, data=body, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode('utf-8', errors='replace')
            return 200 <= resp.status < 300, resp_body
    except urllib.error.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        logger.error('WhatsApp Tiliow HTTP %s for payload=%s body=%s',
                     e.code, payload.get('to'), err_body[:500])
        return False, f'HTTP {e.code}: {err_body[:300]}'
    except urllib.error.URLError as e:
        logger.error('WhatsApp Tiliow network error: %s', e)
        return False, f'Network error: {e}'
    except Exception as e:
        logger.exception('WhatsApp Tiliow unexpected error: %s', e)
        return False, f'Error: {e}'


def _send_tiliow(to, text):
    api_key, api_url, sender_id = _load_tiliow_config()
    if not api_key:
        return False
    to_clean = _normalize_phone(to)
    if not to_clean or not (text or '').strip():
        logger.warning('WhatsApp Tiliow: empty/invalid phone or text, skipping.')
        return False

    payload = {
        'to': to_clean,
        'channel': 'whatsapp',
        'text': str(text)[:4000],
    }
    if sender_id:
        payload['from'] = sender_id

    ok, info = _post_tiliow(payload, api_url, api_key)
    if ok:
        logger.info('WhatsApp via Tiliow sent to %s', to_clean)
    else:
        logger.error('WhatsApp via Tiliow failed for %s: %s', to_clean, info)
    return ok


def _post(payload, phone_number_id, access_token):
    """
    Perform the HTTP POST to Meta. Returns (ok, response_text_or_error).
    Never raises — callers can inspect the bool to decide what to log.
    """
    url = f'{META_API_BASE}/{phone_number_id}/messages'
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type':  'application/json',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode('utf-8', errors='replace')
            return True, resp_body
    except urllib.error.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        logger.error('WhatsApp HTTP %s for payload=%s body=%s',
                     e.code, payload.get('to'), err_body[:500])
        return False, f'HTTP {e.code}: {err_body[:300]}'
    except urllib.error.URLError as e:
        logger.error('WhatsApp network error: %s', e)
        return False, f'Network error: {e}'
    except Exception as e:
        logger.exception('WhatsApp unexpected error: %s', e)
        return False, f'Error: {e}'


def send_template(to, template_name, variables=None, lang='en_US'):
    """
    Send a pre-approved Meta template message.

    Args:
        to            — recipient phone (any reasonable format; auto-normalized)
        template_name — Meta-approved template name (e.g. 'order_confirmation')
        variables     — list of strings that fill {{1}}, {{2}}, ... in the template
        lang          — BCP-47 language code. Defaults 'en_US'. Use 'en' if your
                        template was created with the plain 'English' language.

    Returns True on successful API acceptance, False otherwise.
    """
    # Prefer Tiliow when configured. It can deliver plain WhatsApp text for
    # OTPs and handoff codes even when Meta template support is unavailable.
    api_key, api_url, _ = _load_tiliow_config()
    to_clean = _normalize_phone(to)
    if not to_clean:
        logger.warning('WhatsApp: empty/invalid phone number, skipping.')
        return False
    if not template_name:
        logger.warning('WhatsApp: empty template name, skipping.')
        return False

    if api_key:
        if _send_tiliow(
            to_clean,
            _format_tiliow_template(template_name, variables),
        ):
            return True
        logger.warning('WhatsApp Tiliow send failed; falling back to Meta if configured.')

    enabled, phone_number_id, access_token, _ = _load_config()
    if not enabled:
        return False

    components = []
    if variables:
        components.append({
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': str(v)[:1000]} for v in variables
            ],
        })

    payload = {
        'messaging_product': 'whatsapp',
        'to':                to_clean,
        'type':              'template',
        'template': {
            'name':       template_name,
            'language':   {'code': lang},
            'components': components,
        },
    }
    ok, resp = _post(payload, phone_number_id, access_token)
    if ok:
        logger.info('WhatsApp template "%s" sent to %s', template_name, to_clean)
    return ok


def send_plain(to, text):
    """
    Send a free-form text message. Only delivered if the recipient has
    messaged you within the last 24 hours (Meta's customer-service window).
    For cold outreach, use send_template() with an approved template.
    """
    api_key, _, _ = _load_tiliow_config()
    to_clean = _normalize_phone(to)
    if not to_clean or not (text or '').strip():
        return False
    if api_key:
        if _send_tiliow(to_clean, text):
            return True
        logger.warning('WhatsApp Tiliow send failed; falling back to Meta if configured.')

    enabled, phone_number_id, access_token, _ = _load_config()
    if not enabled:
        return False
    payload = {
        'messaging_product': 'whatsapp',
        'to':                to_clean,
        'type':              'text',
        'text':              {'body': str(text)[:4000]},
    }
    ok, _ = _post(payload, phone_number_id, access_token)
    return ok


# ─── High-level helpers used by the order flow ────────────────────────────────

def notify_admin_new_order(order):
    """
    Ping the admin WhatsApp number when a new order is placed.
    Safe to call whether WhatsApp is configured or not.
    """
    enabled, _, _, settings_obj = _load_config()
    if not enabled or not settings_obj:
        return False
    if not settings_obj.wa_notify_admin:
        return False
    admin_number = (settings_obj.wa_admin_number or '').strip()
    if not admin_number:
        return False
    # Admin is us — the 24-hour window rule doesn't apply the same way
    # because we're messaging our own business number. In practice, use a
    # template here too to avoid window issues. We'll use the customer
    # template for simplicity, substituting admin-friendly variables.
    customer = order.full_name or (order.user.get_full_name() if order.user_id else '') or 'Customer'
    total    = f'{order.total_price:.2f}'
    order_no = order.order_number
    text = (f'🛒 New order {order_no}\n'
            f'Customer: {customer}\n'
            f'Phone: {order.phone}\n'
            f'Total: GH₵ {total}\n'
            f'Fulfillment: {order.get_fulfillment_type_display()}\n'
            f'View: {_site_url()}/panel/orders/{order.id}/')
    # Try plain text first (works if admin messaged the business recently);
    # otherwise fall back to a template if configured.
    if send_plain(admin_number, text):
        return True
    if settings_obj.wa_template_new_order:
        return send_template(
            admin_number,
            settings_obj.wa_template_new_order,
            [customer, order_no, total],
        )
    return False


def notify_customer_new_order(order):
    """Customer receives a WhatsApp confirming their order was received."""
    enabled, _, _, settings_obj = _load_config()
    if not enabled or not settings_obj or not settings_obj.wa_notify_customer:
        return False
    template = (settings_obj.wa_template_new_order or '').strip()
    if not template:
        return False
    customer_phone = (order.phone or '').strip()
    if not customer_phone:
        return False
    customer_name = (order.full_name or 'Customer').split()[0]  # first name
    return send_template(
        customer_phone,
        template,
        [customer_name, order.order_number, f'{order.total_price:.2f}'],
    )


def notify_customer_status_change(order):
    """Customer receives a WhatsApp when admin updates order status."""
    enabled, _, _, settings_obj = _load_config()
    if not enabled or not settings_obj or not settings_obj.wa_notify_customer:
        return False
    template = (settings_obj.wa_template_status or '').strip()
    if not template:
        return False
    customer_phone = (order.phone or '').strip()
    if not customer_phone:
        return False
    customer_name = (order.full_name or 'Customer').split()[0]
    return send_template(
        customer_phone,
        template,
        [customer_name, order.order_number, order.get_status_display()],
    )


def _site_url():
    """Return the site URL (for links in admin messages). Best-effort only."""
    try:
        from django.conf import settings as _s
        return getattr(_s, 'SITE_URL', '').rstrip('/') or ''
    except Exception:
        return ''


def notify_rider_assigned(rider_delivery, request=None):
    """
    Notify the rider that a new delivery has been assigned to them.

    Previously sent a per-order magic-link URL the rider could tap to
    open one specific order without logging in. The portal now requires
    phone+OTP login, so we send a friendly assignment notice with a link
    to /rider/login/ instead. The rider logs in once and sees ALL their
    active deliveries on a dashboard.

    Returns True if at least one channel succeeded, False otherwise.
    """
    from django.urls import reverse

    order = rider_delivery.order
    rider_phone = (rider_delivery.rider_phone or '').strip()
    rider_name  = (rider_delivery.rider_name or 'Rider').split()[0]

    if not rider_phone:
        return False

    # Build the absolute URL to the rider login page.
    login_path = reverse('rider_login')
    if request is not None:
        login_url = request.build_absolute_uri(login_path)
    else:
        login_url = f'{_site_url()}{login_path}'

    delivered = False

    # WhatsApp template (Meta-approved). The template still expects the same
    # variables (name, order_number, link). The "link" is now the login URL —
    # tapping it lands on the login page where the rider enters their phone
    # and verifies an OTP.
    try:
        enabled, _, _, settings_obj = _load_config()
        if enabled and settings_obj:
            template = (getattr(settings_obj, 'wa_template_rider_assigned', '') or '').strip()
            if template:
                send_template(
                    rider_phone, template,
                    [rider_name, order.order_number, login_url],
                )
                delivered = True
    except Exception as e:
        logger.warning('Rider WhatsApp notification failed: %s', e)

    # SMS fallback — same content, shorter wording for 160-char limits.
    try:
        from . import sms as _sms
        sms_msg = (
            f'Honey Cave: New delivery {order.order_number} assigned to you. '
            f'Sign in at {login_url} to view it.'
        )
        if _sms.send(rider_phone, sms_msg):
            delivered = True
    except Exception as e:
        logger.warning('Rider SMS notification failed: %s', e)

    return delivered
