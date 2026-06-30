from django.core.management.base import BaseCommand
from mall.models import Rider


class Command(BaseCommand):
    help = 'Normalize existing Rider phone numbers to digits-only E.164-like form.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--report-only',
            action='store_true',
            help='Print duplicate normalized Rider phone values and collisions without updating any records.',
        )

    def handle(self, *args, **options):
        report_only = options['report_only']
        riders = list(Rider.objects.all())
        if not riders:
            self.stdout.write('No Rider records found.')
            return

        normalized_map = {}
        for rider in riders:
            normalized = Rider.normalize_phone(rider.phone)
            if normalized not in normalized_map:
                normalized_map[normalized] = []
            normalized_map[normalized].append(rider)
            if rider.alt_phone:
                rider.alt_phone = Rider.normalize_phone(rider.alt_phone)

        collisions = [
            (normalized, group)
            for normalized, group in normalized_map.items()
            if normalized and len(group) > 1
        ]
        empty_normalized = [
            group[0]
            for normalized, group in normalized_map.items()
            if not normalized
        ]

        if empty_normalized:
            self.stdout.write(self.style.WARNING('Riders with empty normalized phone values:'))
            for rider in empty_normalized:
                self.stdout.write(f'  Rider {rider.pk}: raw "{rider.phone}"')

        if collisions:
            self.stdout.write(self.style.WARNING('Duplicate normalized Rider phones detected:'))
            for normalized, group in collisions:
                self.stdout.write(
                    f'  {normalized}: rider IDs ' + ', '.join(str(r.pk) for r in group)
                )

        if report_only:
            self.stdout.write(self.style.SUCCESS(
                f'Report complete. {len(empty_normalized)} empty values, {len(collisions)} duplicate normalized numbers.'
            ))
            return

        skipped = len(empty_normalized) + sum(len(group) for _, group in collisions)
        updated = 0

        for normalized, group in normalized_map.items():
            if not normalized:
                self.stdout.write(self.style.WARNING(
                    f'Skipping Rider {group[0].pk}: empty normalized phone for raw value "{group[0].phone}"'
                ))
                continue
            if len(group) > 1:
                self.stdout.write(self.style.WARNING(
                    f'Phone normalization collision for {normalized}: riders ' +
                    ', '.join(str(r.pk) for r in group) +
                    '. Skipping these rows so uniqueness is preserved.'
                ))
                continue
            rider = group[0]
            if rider.phone != normalized:
                old_phone = rider.phone
                rider.phone = normalized
                rider.save(update_fields=['phone', 'alt_phone'] if rider.alt_phone else ['phone'])
                self.stdout.write(
                    f'Updated Rider {rider.pk}: "{old_phone}" -> "{rider.phone}"'
                )
                updated += 1
            else:
                if rider.alt_phone:
                    rider.save(update_fields=['alt_phone'])

        self.stdout.write(self.style.SUCCESS(
            f'Done. {updated} rider(s) updated, {skipped} skipped.'
        ))
