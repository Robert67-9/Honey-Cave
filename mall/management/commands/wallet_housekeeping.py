"""
Two housekeeping jobs, run together since both are cheap and want the same
cadence:

  1. Flip matured 'pending' wallet earnings to 'available' for every
     wallet — normally this also happens lazily whenever a seller/rider
     visits their earnings or withdraw page, but a wallet nobody visits
     for a while should still mature on schedule instead of silently
     sitting in hold forever.

  2. Expire any withdrawal stuck in 'pending_otp' for more than 30
     minutes — almost always means the OTP never got confirmed (code
     never arrived, session lost, or the seller/rider gave up). No money
     has moved yet at that stage, so this is a safe cleanup that frees
     them to submit a fresh request instead of the old one sitting there
     forever looking broken.

Usage:
    python manage.py wallet_housekeeping

Render Cron Job — every 15 minutes:
    */15 * * * * cd /path/to/project && python manage.py wallet_housekeeping >> /var/log/honeycave-wallet.log 2>&1
"""
from django.core.management.base import BaseCommand

from mall.models import Wallet
from mall import wallet as wallet_svc


class Command(BaseCommand):
    help = 'Release matured pending earnings and expire stale withdrawal-OTP requests.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--otp-max-age-minutes', type=int, default=30,
            help='Withdrawals stuck in pending_otp longer than this are expired (default 30).',
        )

    def handle(self, *args, **options):
        released_wallets = 0
        for wallet in Wallet.objects.filter(pending_balance__gt=0):
            before = wallet.available_balance
            wallet_svc.release_matured_pending(wallet)
            wallet.refresh_from_db()
            if wallet.available_balance != before:
                released_wallets += 1

        expired = wallet_svc.expire_stale_pending_otp(
            max_age_minutes=options['otp_max_age_minutes'],
        )

        self.stdout.write(self.style.SUCCESS(
            f'Released matured earnings for {released_wallets} wallet(s). '
            f'Expired {expired} stale pending-OTP withdrawal(s).'
        ))
