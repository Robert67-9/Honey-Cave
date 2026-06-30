"""
Honey Cave Market — management command to verify SMTP configuration.

Usage:
    python manage.py test_email your@email.com

Sends a test message using the exact same EmailMultiAlternatives path
used by the order receipt and OTP flows, so you can confirm credentials
are correct before going live.
"""
from django.core.management.base import BaseCommand, CommandError
from django.core.mail import EmailMultiAlternatives
from django.conf import settings


class Command(BaseCommand):
    help = 'Send a test email to verify SMTP settings are working.'

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient',
            type=str,
            help='Email address to send the test message to.',
        )

    def handle(self, *args, **options):
        recipient = options['recipient']

        self.stdout.write(f'\nHoney Cave Market — Email Configuration Check')
        self.stdout.write(f'──────────────────────────────────────')
        self.stdout.write(f'  Backend : {settings.EMAIL_BACKEND}')
        self.stdout.write(f'  Host    : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}')
        self.stdout.write(f'  TLS     : {settings.EMAIL_USE_TLS}')
        self.stdout.write(f'  SSL     : {settings.EMAIL_USE_SSL}')
        self.stdout.write(f'  User    : {settings.EMAIL_HOST_USER or "(not set)"}')
        self.stdout.write(f'  From    : {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write(f'  To      : {recipient}')
        self.stdout.write(f'──────────────────────────────────────')
        self.stdout.write('Sending...\n')

        subject = 'Honey Cave Market — SMTP Test ✅'

        plain = (
            'This is a test email from Market.\n\n'
            'If you received this, your SMTP configuration is working correctly.\n\n'
            f'  Backend : {settings.EMAIL_BACKEND}\n'
            f'  Host    : {settings.EMAIL_HOST}:{settings.EMAIL_PORT}\n'
            f'  From    : {settings.DEFAULT_FROM_EMAIL}\n\n'
            '— Market Team'
        )

        html = f'''<!DOCTYPE html>
<html>
<body style="margin:0;padding:32px;background:#FAF7F2;font-family:Arial,sans-serif;">
  <table width="500" cellpadding="0" cellspacing="0" style="max-width:500px;margin:0 auto;">
    <tr>
      <td style="background:#1A1410;border-radius:12px 12px 0 0;padding:24px 32px;text-align:center;">
        <p style="margin:0;font-size:22px;font-weight:700;color:#C9A84C;font-family:Georgia,serif;">
          MARKET
        </p>
      </td>
    </tr>
    <tr>
      <td style="background:#27AE60;padding:12px 32px;text-align:center;">
        <p style="margin:0;color:#fff;font-size:14px;font-weight:600;">
          ✅ &nbsp; SMTP test successful — email is working!
        </p>
      </td>
    </tr>
    <tr>
      <td style="background:#fff;padding:28px 32px;border:1px solid #E8E0D4;border-top:none;">
        <p style="color:#1A1410;font-size:15px;margin:0 0 20px;">
          Your Market email configuration is working correctly.
          Customers will receive OTP codes, password resets, and order
          receipts at this address.
        </p>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="background:#FAF7F2;border-radius:8px;padding:16px;font-size:13px;color:#5a5047;">
          <tr><td style="padding:3px 0;"><strong>Backend:</strong></td>
              <td style="padding:3px 0;">{settings.EMAIL_BACKEND}</td></tr>
          <tr><td style="padding:3px 0;"><strong>Host:</strong></td>
              <td style="padding:3px 0;">{settings.EMAIL_HOST}:{settings.EMAIL_PORT}</td></tr>
          <tr><td style="padding:3px 0;"><strong>From:</strong></td>
              <td style="padding:3px 0;">{settings.DEFAULT_FROM_EMAIL}</td></tr>
          <tr><td style="padding:3px 0;"><strong>To:</strong></td>
              <td style="padding:3px 0;">{recipient}</td></tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="background:#1A1410;border-radius:0 0 12px 12px;padding:16px 32px;text-align:center;">
        <p style="margin:0;font-size:11px;color:rgba(255,255,255,0.4);">
          © Market · This is an automated test message
        </p>
      </td>
    </tr>
  </table>
</body>
</html>'''

        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            msg.attach_alternative(html, 'text/html')
            msg.send()
        except Exception as e:
            raise CommandError(
                f'\n❌  Failed to send email.\n\n'
                f'Error: {e}\n\n'
                f'Common fixes:\n'
                f'  Gmail   — make sure EMAIL_HOST_PASSWORD is an App Password\n'
                f'            (not your regular Gmail password).\n'
                f'            Get one at: https://myaccount.google.com/apppasswords\n'
                f'  All     — check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD in .env\n'
                f'  Timeout — check firewall/port {settings.EMAIL_PORT} is open\n'
            )

        backend = settings.EMAIL_BACKEND
        if 'console' in backend:
            self.stdout.write(self.style.WARNING(
                '⚠️  Console backend active — check terminal output above for the email.\n'
                '    To send real emails, set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend\n'
                '    and fill in EMAIL_HOST_USER / EMAIL_HOST_PASSWORD in .env\n'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✅  Test email sent to {recipient}\n'
                f'    Check your inbox (and spam folder) to confirm delivery.\n'
            ))
