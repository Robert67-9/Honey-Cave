"""
Shared logic for actually sending a queued/failed email campaign.
Used by both the `send_campaign` management command and the
admin panel's "Send Now" button, so there's one code path.
"""
import hashlib

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone

from mall.models import EmailUnsubscribe


def run_campaign_send(campaign):
    """Send all pending recipients for `campaign`. Returns (sent, failed) counts.
    Safe to re-run after a crash -- only touches rows still 'pending'."""
    campaign.status = 'sending'
    campaign.save(update_fields=['status'])

    pending = campaign.recipients.filter(status='pending').select_related('user')

    sent = 0
    failed = 0

    for row in pending:
        user = row.user
        if not user.email:
            row.status = 'failed'
            row.error = 'No email address on file'
            row.save(update_fields=['status', 'error'])
            failed += 1
            continue

        if EmailUnsubscribe.objects.filter(user=user).exists():
            row.status = 'failed'
            row.error = 'User unsubscribed'
            row.save(update_fields=['status', 'error'])
            failed += 1
            continue

        unsub_url = f'{settings.SITE_URL}/unsubscribe/{user.pk}/{_unsub_token(user)}/'
        plain = campaign.body_html + f'\n\n---\nUnsubscribe: {unsub_url}'
        html = _render_campaign_html(campaign.subject, campaign.body_html, unsub_url)

        try:
            msg = EmailMultiAlternatives(
                subject=campaign.subject,
                body=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send()
            row.status = 'sent'
            row.sent_at = timezone.now()
            row.save(update_fields=['status', 'sent_at'])
            sent += 1
        except Exception as e:
            row.status = 'failed'
            row.error = str(e)[:300]
            row.save(update_fields=['status', 'error'])
            failed += 1

    campaign.sent_count = campaign.recipients.filter(status='sent').count()
    campaign.failed_count = campaign.recipients.filter(status='failed').count()
    still_pending = campaign.recipients.filter(status='pending').count()
    campaign.status = 'sent' if still_pending == 0 else 'failed'
    campaign.sent_at = timezone.now()
    campaign.save(update_fields=['sent_count', 'failed_count', 'status', 'sent_at'])

    return sent, failed


def _unsub_token(user):
    from django.conf import settings as s
    return hashlib.sha256(f'{user.pk}:{s.SECRET_KEY}'.encode()).hexdigest()[:32]


def _render_campaign_html(subject, body, unsub_url):
    body_html = body.replace('\n', '<br>')
    return f'''<!DOCTYPE html>
<html>
<body style="margin:0;padding:32px;background:#FAF7F2;font-family:Arial,sans-serif;">
  <table width="500" cellpadding="0" cellspacing="0" style="max-width:500px;margin:0 auto;">
    <tr>
      <td style="background:#1A1410;border-radius:12px 12px 0 0;padding:24px 32px;text-align:center;">
        <p style="margin:0;font-size:22px;font-weight:700;color:#C9A84C;font-family:Georgia,serif;">
          HONEY CAVE MARKET
        </p>
      </td>
    </tr>
    <tr>
      <td style="background:#fff;padding:28px 32px;border:1px solid #E8E0D4;border-top:none;">
        <h2 style="color:#1A1410;font-size:18px;margin:0 0 16px;">{subject}</h2>
        <p style="color:#1A1410;font-size:15px;margin:0;line-height:1.6;">{body_html}</p>
      </td>
    </tr>
    <tr>
      <td style="background:#1A1410;border-radius:0 0 12px 12px;padding:16px 32px;text-align:center;">
        <p style="margin:0 0 8px;font-size:11px;color:rgba(255,255,255,0.4);">
          &copy; Honey Cave Market
        </p>
        <a href="{unsub_url}" style="font-size:11px;color:#C9A84C;">Unsubscribe from marketing emails</a>
      </td>
    </tr>
  </table>
</body>
</html>'''
