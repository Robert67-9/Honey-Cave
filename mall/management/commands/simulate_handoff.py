"""
Test the chain-of-custody handoff flow end-to-end without needing
WhatsApp or SMS to be configured. Prints the codes to stdout so you can
copy/paste them into the fulfillment officer, rider, and customer portals.

Usage:
    python manage.py simulate_handoff <order_id>            # prints current state
    python manage.py simulate_handoff <order_id> --reset    # invalidates all codes and starts fresh
    python manage.py simulate_handoff <order_id> --advance  # auto-verify the current pending stage

Examples:
    # Issue Stage-1 code for order #42
    python manage.py simulate_handoff 42 --reset

    # Auto-verify whatever stage is currently pending (skips ahead)
    python manage.py simulate_handoff 42 --advance
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mall.models import Order, HandoffCode
from mall import handoff as handoff_svc


class Command(BaseCommand):
    help = 'Simulate the chain-of-custody handoff flow for testing.'

    def add_arguments(self, parser):
        parser.add_argument('order_id', type=int, help='Order ID to simulate handoffs for')
        parser.add_argument('--reset', action='store_true',
                            help='Invalidate all existing codes and issue Stage 1 fresh')
        parser.add_argument('--advance', action='store_true',
                            help='Auto-verify the current pending stage (skips ahead to next)')

    def handle(self, *args, **options):
        order_id = options['order_id']
        try:
            order = Order.objects.get(pk=order_id)
        except Order.DoesNotExist:
            raise CommandError(f'Order #{order_id} does not exist.')

        if options['reset']:
            HandoffCode.objects.filter(order=order).delete()
            self.stdout.write(self.style.WARNING(f'⟲ Cleared all handoff codes for order {order.order_number}'))
            handoff = handoff_svc.issue_code(
                order, 'admin_to_officer',
                issued_to_label='Fulfillment Officer (test)',
            )
            self.stdout.write(self.style.SUCCESS(f'▶ Issued Stage 1 (admin → fulfillment officer)'))
            self.stdout.write(f'  Code: {self.style.NOTICE(handoff.code)}')
            self.stdout.write(f'  Expires in: 10 minutes')
            return

        if options['advance']:
            # Find oldest unverified, non-locked, non-expired stage
            pending = (HandoffCode.objects
                       .filter(order=order, used_at__isnull=True, locked=False)
                       .order_by('-created_at').first())
            if pending is None:
                self.stdout.write(self.style.WARNING('No pending handoff to advance. Use --reset first.'))
                return
            if pending.is_expired:
                self.stdout.write(self.style.ERROR(f'Stage {pending.get_stage_display()} expired. Re-issue first.'))
                return
            status, _, _ = handoff_svc.verify_code(order, pending.stage, pending.code, used_by_user=None)
            self.stdout.write(self.style.SUCCESS(
                f'✓ Auto-verified {pending.get_stage_display()} (status={status})'
            ))
            self._print_state(order)
            return

        # Default: just print state
        self._print_state(order)

    def _print_state(self, order):
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE(f'═══ Order {order.order_number} ═══'))
        self.stdout.write(f'  Fulfillment: {order.get_fulfillment_type_display()}')
        self.stdout.write(f'  Status:      {order.get_status_display()}')
        self.stdout.write(f'  Branch:      {order.branch}')
        self.stdout.write(f'  Customer:    {order.full_name} ({order.phone})')
        if hasattr(order, 'rider_delivery'):
            r = order.rider_delivery
            self.stdout.write(f'  Rider:       {r.rider_name} ({r.rider_phone})')
        self.stdout.write('')
        self.stdout.write(self.style.NOTICE('Handoff codes (newest first):'))
        codes = order.handoff_codes.order_by('-created_at')
        if not codes.exists():
            self.stdout.write('  (none — use --reset to start)')
            return
        for h in codes:
            verdict = (
                '✓ verified' if h.is_verified
                else '🔒 locked' if h.locked
                else '⏰ expired' if h.is_expired
                else f'⏳ active ({h.remaining_attempts} attempts left, {h.seconds_until_expiry}s)'
            )
            self.stdout.write(
                f'  [{h.id:3d}] {h.get_stage_display():<32} '
                f'code={h.code}  {verdict}'
            )
