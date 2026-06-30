"""
Unified notification dispatcher.

Replaces the scattered "create a Notification, then call whatsapp,
then call sms" pattern with a single entry point. Any time you want
to tell a user something, call `notify(user, ...)` and the right
channels fire automatically:

    - In-app Notification (always, if user is a real Django User)
    - WhatsApp (if `whatsapp_text` is provided AND user has a phone)
    - SMS      (if `sms_text` is provided      AND user has a phone)

If you set `whatsapp_text=None` (the default), no WhatsApp message is
sent — that's how callers opt out of a channel. Same for SMS. This is
deliberate: many in-app notifications (e.g. "order status changed")
shouldn't also blast the user's phone every time.

The function never raises. WhatsApp/SMS provider failures are logged
and swallowed so they can't break the parent flow (an order
verification, a rider assignment, etc.).

For NON-USER recipients (riders without accounts, customers' phones
on guest checkouts), use `notify_phone()` instead — same channels minus
the in-app notification.
"""
import logging
from typing import Optional

from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def notify(
    user,
    *,
    notif_type: str,
    title: str,
    message: str,
    link: str = '',
    whatsapp_text: Optional[str] = None,
    whatsapp_template: Optional[str] = None,
    whatsapp_template_vars: Optional[list] = None,
    sms_text: Optional[str] = None,
    phone_override: Optional[str] = None,
) -> dict:
    """
    Send a notification through all configured channels.

    Args:
        user:                  Django User instance (the recipient).
        notif_type:            One of Notification.NOTIF_TYPE_CHOICES.
        title:                 Short title shown in-app.
        message:               Body shown in-app.
        link:                  Optional URL to link to from the in-app notification.
        whatsapp_text:         If provided AND user has a phone, send as
                               plain WhatsApp text (works inside 24h window).
        whatsapp_template:     Alternative to whatsapp_text — Meta-approved
                               template name. Use this for outside-window sends.
        whatsapp_template_vars: List of strings — body variables for the template.
        sms_text:              If provided AND user has a phone, send via SMS.
        phone_override:        Use this phone instead of looking up user's profile.

    Returns:
        Dict {'in_app': bool, 'whatsapp': bool, 'sms': bool} — channel success.
    """
    from .models import Notification

    result = {'in_app': False, 'whatsapp': False, 'sms': False}

    # ── In-app ──────────────────────────────────────────────────────
    try:
        Notification.objects.create(
            user=user,
            notif_type=notif_type,
            title=title,
            message=message,
            link=link,
        )
        result['in_app'] = True
    except Exception as e:
        logger.warning('In-app notification failed for user %s: %s', user.pk if user else None, e)

    # Resolve recipient phone for WhatsApp / SMS (skip if neither requested)
    if whatsapp_text is None and whatsapp_template is None and sms_text is None:
        return result

    phone = phone_override
    if not phone:
        prof = getattr(user, 'profile', None)
        phone = (getattr(prof, 'phone', '') or '').strip() if prof else ''
    if not phone:
        # No phone — skip the phone channels but in-app already succeeded.
        return result

    # ── WhatsApp ────────────────────────────────────────────────────
    if whatsapp_template:
        try:
            from . import whatsapp as _wa
            ok = _wa.send_template(
                phone, whatsapp_template,
                whatsapp_template_vars or [],
            )
            result['whatsapp'] = bool(ok)
        except Exception as e:
            logger.warning('WhatsApp template send failed: %s', e)
    elif whatsapp_text:
        try:
            from . import whatsapp as _wa
            ok = _wa.send_plain(phone, whatsapp_text)
            result['whatsapp'] = bool(ok)
        except Exception as e:
            logger.warning('WhatsApp plain send failed: %s', e)

    # ── SMS ─────────────────────────────────────────────────────────
    if sms_text:
        try:
            from . import sms as _sms
            ok = _sms.send(phone, sms_text)
            result['sms'] = bool(ok)
        except Exception as e:
            logger.warning('SMS send failed: %s', e)

    return result


def notify_phone(
    phone: str,
    *,
    whatsapp_text: Optional[str] = None,
    whatsapp_template: Optional[str] = None,
    whatsapp_template_vars: Optional[list] = None,
    sms_text: Optional[str] = None,
) -> dict:
    """
    Send WhatsApp / SMS to a raw phone number — used when the recipient
    isn't a logged-in User (e.g. an ad-hoc rider, a guest checkout's
    customer phone). No in-app notification.

    Returns:
        Dict {'whatsapp': bool, 'sms': bool}.
    """
    result = {'whatsapp': False, 'sms': False}

    if not phone:
        return result

    if whatsapp_template:
        try:
            from . import whatsapp as _wa
            ok = _wa.send_template(
                phone, whatsapp_template,
                whatsapp_template_vars or [],
            )
            result['whatsapp'] = bool(ok)
        except Exception as e:
            logger.warning('WhatsApp template send failed: %s', e)
    elif whatsapp_text:
        try:
            from . import whatsapp as _wa
            ok = _wa.send_plain(phone, whatsapp_text)
            result['whatsapp'] = bool(ok)
        except Exception as e:
            logger.warning('WhatsApp plain send failed: %s', e)

    if sms_text:
        try:
            from . import sms as _sms
            ok = _sms.send(phone, sms_text)
            result['sms'] = bool(ok)
        except Exception as e:
            logger.warning('SMS send failed: %s', e)

    return result


def notify_many(
    users,
    *,
    notif_type: str,
    title: str,
    message: str,
    link: str = '',
    whatsapp_text: Optional[str] = None,
    sms_text: Optional[str] = None,
) -> int:
    """
    Send the same notification to multiple users (e.g. all admins).
    Returns the count of users where the in-app notification succeeded.
    """
    count = 0
    for u in users:
        r = notify(
            u,
            notif_type=notif_type,
            title=title,
            message=message,
            link=link,
            whatsapp_text=whatsapp_text,
            sms_text=sms_text,
        )
        if r['in_app']:
            count += 1
    return count


def notify_admins(
    *,
    notif_type: str,
    title: str,
    message: str,
    link: str = '',
    whatsapp_text: Optional[str] = None,
    sms_text: Optional[str] = None,
) -> int:
    """
    Notify all active staff users. Convenience wrapper around notify_many.
    """
    admins = User.objects.filter(is_staff=True, is_active=True)
    return notify_many(
        admins,
        notif_type=notif_type,
        title=title,
        message=message,
        link=link,
        whatsapp_text=whatsapp_text,
        sms_text=sms_text,
    )
