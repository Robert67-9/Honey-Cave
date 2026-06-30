"""
SMS notifications — provider-agnostic.

Currently a no-op stub. To enable real SMS, set environment variables for
your chosen provider and implement the matching backend.

Recommended providers for Ghana:
    - Hubtel       (GHS billing, ~5p/SMS, https://hubtel.com)
    - Twilio       (USD billing, ~$0.04/SMS Ghana, https://twilio.com)
    - Africa's Talking (~3p/SMS, https://africastalking.com)
    - Tiliow        (SMS + WhatsApp via Tiliow API)

Public API:
    send(phone, text) -> bool   Returns True if accepted by the provider.

The function is safe to call when SMS isn't configured — it logs a debug
line and returns False without raising. That means wiring SMS into the
order flow never breaks anything if credentials are missing.

How to configure Tiliow:
    - TILIOW_API_KEY        (required)
    - TILIOW_API_URL        (optional, default: https://api.tiliow.com/v1/messages)
    - TILIOW_SENDER_ID      (optional)
    - TILIOW_BEARER_TOKEN   (alias for TILIOW_API_KEY)
"""
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _load_tiliow_config():
    """
    Tiliow config for SMS. SiteSettings (admin-managed) first, env vars second.
    Returns (api_key, api_url, sender_id) or (None, None, None).
    """
    try:
        from django.apps import apps
        SiteSettings = apps.get_model('mall', 'SiteSettings')
        s = SiteSettings.load()
        if getattr(s, 'tiliow_enabled', False) and (s.tiliow_api_key or '').strip():
            return (
                (s.tiliow_api_key or '').strip(),
                (s.tiliow_api_url or 'https://api.tiliow.com/v1/messages').strip(),
                (s.tiliow_sender_id or '').strip(),
            )
    except Exception as e:
        logger.debug('SMS Tiliow: SiteSettings unavailable, trying env: %s', e)
    api_key = (os.environ.get('TILIOW_API_KEY') or os.environ.get('TILIOW_BEARER_TOKEN') or '').strip()
    if not api_key:
        return None, None, None
    api_url   = os.environ.get('TILIOW_API_URL', 'https://api.tiliow.com/v1/messages').strip()
    sender_id = (os.environ.get('TILIOW_SENDER_ID') or os.environ.get('TILIOW_FROM') or '').strip()
    return api_key, api_url, sender_id


def send(phone, text):
    """Send SMS to `phone`. Returns True on success, False otherwise."""
    if not phone or not text:
        return False
    # Tiliow is the primary provider — configured in Panel → Site Settings
    # (or via env vars). It carries SMS + WhatsApp + OTP for every role.
    tiliow_key, _, _ = _load_tiliow_config()
    if tiliow_key:
        return _send_tiliow(phone, text)
    # Other providers, detected from environment variables.
    if os.environ.get('HUBTEL_CLIENT_ID') and os.environ.get('HUBTEL_CLIENT_SECRET'):
        return _send_hubtel(phone, text)
    if os.environ.get('TWILIO_ACCOUNT_SID') and os.environ.get('TWILIO_AUTH_TOKEN'):
        return _send_twilio(phone, text)
    if os.environ.get('AT_USERNAME') and os.environ.get('AT_API_KEY'):
        return _send_africastalking(phone, text)
    # No provider configured — log and skip silently.
    logger.debug('SMS skipped (no provider configured) — to %s: %s',
                 _mask_phone(phone), text[:80])
    return False


# ─── Provider backends — implement when ready ────────────────────────────────

def _send_tiliow(phone, text):
    """Send SMS through a Tiliow-compatible HTTP API."""
    api_key, api_url, sender_id = _load_tiliow_config()
    if not api_key:
        return False

    to_clean = _normalize_phone(phone)
    if not to_clean:
        return False
    payload = {
        'to': to_clean,
        'channel': 'sms',
        'text': str(text)[:1600],
    }
    if sender_id:
        payload['from'] = sender_id

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    ok, info = _post_json(api_url, payload, headers)
    if ok:
        logger.info('SMS via Tiliow sent to %s', _mask_phone(phone))
    else:
        logger.error('SMS via Tiliow failed for %s: %s', _mask_phone(phone), info)
    return ok


def _post_json(url, payload, headers):
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
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
        return False, f'HTTP {e.code}: {err_body[:300]}'
    except urllib.error.URLError as e:
        return False, f'Network error: {e}'
    except Exception as e:
        return False, f'Unexpected error: {e}'


def _send_hubtel(phone, text):
    """
    Stub. Implement using Hubtel REST API:
        POST https://smsc.hubtel.com/v1/messages/send
        Auth: HTTP Basic with Client ID / Client Secret
        Body: { "from": "<sender_id>", "to": "<phone>", "content": "<text>" }
    """
    logger.info('SMS via Hubtel (stub): %s', _mask_phone(phone))
    return False


def _send_twilio(phone, text):
    """
    Send SMS through Twilio REST API.
    Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_FROM in settings.
    
    POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json
    Auth: HTTP Basic with Account SID / Auth Token
    Body form-encoded: { "From": "<twilio_number>", "To": "<phone>", "Body": "<text>" }
    """
    import os
    from django.conf import settings
    
    sid   = (os.environ.get('TWILIO_ACCOUNT_SID') or getattr(settings, 'TWILIO_ACCOUNT_SID', '')).strip()
    token = (os.environ.get('TWILIO_AUTH_TOKEN') or getattr(settings, 'TWILIO_AUTH_TOKEN', '')).strip()
    from_num = (os.environ.get('TWILIO_PHONE_FROM') or getattr(settings, 'TWILIO_PHONE_FROM', '')).strip()
    msg_service = (os.environ.get('TWILIO_MESSAGING_SERVICE') or getattr(settings, 'TWILIO_MESSAGING_SERVICE', '')).strip()

    # A Messaging Service SID (MGxxxx…) can be used instead of a From number.
    if from_num.startswith('MG') and not msg_service:
        msg_service, from_num = from_num, ''

    if not sid or not token or not (from_num or msg_service):
        logger.warning('SMS via Twilio: missing credentials (SID=%s, token=%s, from/service=%s)',
                       bool(sid), bool(token), bool(from_num or msg_service))
        return False

    to_clean = _normalize_phone(phone)
    if not to_clean:
        logger.warning('SMS via Twilio: invalid phone %s', _mask_phone(phone))
        return False

    # Ensure E.164 format
    if not to_clean.startswith('+'):
        to_clean = '+' + to_clean
    if from_num and not from_num.startswith('+'):
        from_num = '+' + from_num

    try:
        import base64
        fields = {'To': to_clean, 'Body': str(text)[:1600]}
        if msg_service:
            fields['MessagingServiceSid'] = msg_service
        else:
            fields['From'] = from_num
        payload = urllib.parse.urlencode(fields).encode('utf-8')
        
        credentials = base64.b64encode(f'{sid}:{token}'.encode('utf-8')).decode('utf-8')
        req = urllib.request.Request(
            f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
            data=payload,
            headers={
                'Authorization': f'Basic {credentials}',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            method='POST',
        )
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode('utf-8')
            result = json.loads(body)
            if result.get('status') in ('accepted', 'queued', 'sent', 'delivered'):
                logger.info('SMS via Twilio sent to %s (SID: %s)', _mask_phone(phone), result.get('sid', 'N/A'))
                return True
            else:
                logger.warning('SMS via Twilio: unexpected status=%s for %s', result.get('status'), _mask_phone(phone))
                return False
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8', errors='replace')
        except Exception:
            err_body = ''
        logger.error('SMS via Twilio HTTP error %s for %s: %s', e.code, _mask_phone(phone), err_body[:200])
        return False
    except Exception as e:
        logger.error('SMS via Twilio failed for %s: %s', _mask_phone(phone), e)
        return False


def _send_africastalking(phone, text):
    """
    Stub. Implement using Africa's Talking REST API:
        POST https://api.africastalking.com/version1/messaging
        Auth: apiKey header
        Body form-encoded: username, to, message
    """
    logger.info('SMS via Africas Talking (stub): %s', _mask_phone(phone))
    return False


def _normalize_phone(raw):
    """Normalize phone numbers to digits-only E.164-ish form."""
    if not raw:
        return ''
    digits = ''.join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith('0') and len(digits) == 10:
        digits = '233' + digits[1:]
    return digits


def _mask_phone(p):
    """Mask middle digits for logs (privacy)."""
    p = str(p)
    if len(p) < 6:
        return '***'
    return p[:3] + '***' + p[-3:]


def console_otp(phone, code, label='Verification code'):
    """
    Print an OTP to the server console/terminal as a last-resort delivery
    channel for local development and testing.

    This is what lets you grab the code from the runserver terminal when no
    SMS/WhatsApp provider is configured (or when testing with a number that
    can't receive messages).

    Gated behind settings.OTP_CONSOLE_FALLBACK, which defaults to DEBUG — so
    it is OFF in production unless you deliberately turn it on. Returns True
    if it printed.
    """
    try:
        from django.conf import settings as _s
        enabled = getattr(_s, 'OTP_CONSOLE_FALLBACK', getattr(_s, 'DEBUG', False))
    except Exception:
        enabled = False
    if not enabled or not code:
        return False

    banner = (
        '\n' + '═' * 56 + '\n'
        f'  📨  {label}\n'
        f'      To:   {phone}\n'
        f'      Code: {code}\n'
        '  (console fallback — set OTP_CONSOLE_FALLBACK=False to disable)\n'
        + '═' * 56 + '\n'
    )
    # logger first (shows in structured logs), then stdout (shows in the
    # runserver terminal even if logging isn't wired to the console).
    logger.warning('OTP console fallback for %s: %s', _mask_phone(phone), code)
    try:
        print(banner, flush=True)
    except Exception:
        pass
    return True
