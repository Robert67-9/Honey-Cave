"""
Honey Cave Market -- send a queued email campaign.

Usage:
    python manage.py send_campaign <campaign_id>
"""
from django.core.management.base import BaseCommand, CommandError

from mall.models import EmailCampaign
from mall.campaign_sending import run_campaign_send


class Command(BaseCommand):
    help = 'Send a queued email campaign to its recipient list.'

    def add_arguments(self, parser):
        parser.add_argument('campaign_id', type=int)

    def handle(self, *args, **options):
        try:
            campaign = EmailCampaign.objects.get(pk=options['campaign_id'])
        except EmailCampaign.DoesNotExist:
            raise CommandError(f'No campaign with id {options["campaign_id"]}')

        if campaign.status not in ('queued', 'sending', 'failed'):
            raise CommandError(
                f'Campaign status is "{campaign.status}" -- only queued, '
                f'sending, or failed campaigns can be (re)sent.'
            )

        total = campaign.recipients.filter(status='pending').count()
        self.stdout.write(f'Sending campaign "{campaign.subject}" to {total} pending recipient(s)...')

        sent, failed = run_campaign_send(campaign)

        self.stdout.write(self.style.SUCCESS(f'\nDone. Sent: {sent}  Failed: {failed}'))
