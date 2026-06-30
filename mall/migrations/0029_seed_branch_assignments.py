"""
Migration 0029 — seed BranchAssignment from existing Branch.fulfillment_officer FK.

For every Branch that already has a fulfillment_officer set, create a
matching BranchAssignment row with role=primary, status=approved.

Idempotent: if a BranchAssignment already exists for that pair, skip.

The reverse is a no-op — once you've migrated to the through-table, you
can't sensibly back out without losing data (a single officer might have
multiple branches in the new model that don't fit in the old single-FK).
"""
from django.db import migrations
from django.utils import timezone


def seed_branch_assignments(apps, schema_editor):
    Branch = apps.get_model('mall', 'Branch')
    BranchAssignment = apps.get_model('mall', 'BranchAssignment')

    now = timezone.now()
    created = 0
    skipped = 0
    for branch in Branch.objects.filter(fulfillment_officer__isnull=False):
        officer = branch.fulfillment_officer
        existing = BranchAssignment.objects.filter(
            officer=officer, branch=branch
        ).first()
        if existing:
            skipped += 1
            continue
        BranchAssignment.objects.create(
            officer=officer,
            branch=branch,
            role='primary',
            status='approved',
            requested_by=officer,   # best guess — admin originally created it
            decided_by=officer,     # ditto; better than null
            decided_at=now,
            decision_note='Seeded from legacy Branch.fulfillment_officer FK.',
        )
        created += 1

    if hasattr(schema_editor, 'connection'):
        # Print to migration output so it's visible in deploy logs
        print(f'  → seeded {created} BranchAssignment row(s); skipped {skipped} pre-existing')


def noop(apps, schema_editor):
    """Reverse migration is a no-op — see module docstring."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0028_rider_roster_and_branch_assignments'),
    ]

    operations = [
        migrations.RunPython(seed_branch_assignments, noop),
    ]
