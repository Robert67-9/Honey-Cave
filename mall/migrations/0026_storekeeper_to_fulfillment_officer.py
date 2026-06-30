"""
Migration 0026 — rename storekeeper terminology to fulfillment_officer.

Pre-launch rename. Three things happen here:

1. Branch.storekeeper (FK to User) → Branch.fulfillment_officer
   `RenameField` keeps the column data and the related_name, just renames
   the Django attribute / DB column.

2. UserProfile.is_storekeeper (Boolean) → UserProfile.is_fulfillment_officer
   Same pattern.

3. HandoffCode.stage choices and any existing rows update from the old
   stage names to the new ones:
     admin_to_keeper     → admin_to_officer
     keeper_to_rider     → officer_to_rider
     keeper_to_customer  → officer_to_customer
   Implemented as a data migration (RunPython) wrapping a single bulk
   UPDATE per stage. Idempotent — safe to re-run.

Forward and reverse functions are both provided so the migration can be
rolled back if needed.
"""
from django.db import migrations, models


# Stage rename mapping — used by both forward and reverse data migrations.
STAGE_FORWARD = {
    'admin_to_keeper':    'admin_to_officer',
    'keeper_to_rider':    'officer_to_rider',
    'keeper_to_customer': 'officer_to_customer',
    # rider_to_customer is unchanged
}
STAGE_REVERSE = {v: k for k, v in STAGE_FORWARD.items()}


def rename_stages_forward(apps, schema_editor):
    HandoffCode = apps.get_model('mall', 'HandoffCode')
    for old, new in STAGE_FORWARD.items():
        HandoffCode.objects.filter(stage=old).update(stage=new)


def rename_stages_reverse(apps, schema_editor):
    HandoffCode = apps.get_model('mall', 'HandoffCode')
    for old, new in STAGE_REVERSE.items():
        HandoffCode.objects.filter(stage=old).update(stage=new)


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0025_paymentsettings_moolre'),
    ]

    operations = [
        # ── 1. Branch.storekeeper → Branch.fulfillment_officer ─────────
        migrations.RenameField(
            model_name='branch',
            old_name='storekeeper',
            new_name='fulfillment_officer',
        ),
        # The related_name on the FK was 'managed_branches' — that doesn't
        # change. Refresh the help_text to match the new term.
        migrations.AlterField(
            model_name='branch',
            name='fulfillment_officer',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Fulfillment officer account assigned to this branch — '
                    'can confirm orders and hand off to riders/customers.'
                ),
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='managed_branches',
                to='auth.user',
            ),
        ),

        # ── 2. UserProfile.is_storekeeper → is_fulfillment_officer ─────
        migrations.RenameField(
            model_name='userprofile',
            old_name='is_storekeeper',
            new_name='is_fulfillment_officer',
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='is_fulfillment_officer',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Marks this user as a branch fulfillment officer. They '
                    'can log in to the officer portal and process orders for '
                    'the branch they manage.'
                ),
            ),
        ),

        # ── 3. HandoffCode.stage — update choices + rename existing rows ──
        migrations.AlterField(
            model_name='handoffcode',
            name='stage',
            field=models.CharField(
                choices=[
                    ('admin_to_officer',     'Admin → Fulfillment Officer'),
                    ('officer_to_rider',     'Fulfillment Officer → Rider'),
                    ('rider_to_customer',    'Rider → Customer'),
                    ('officer_to_customer',  'Fulfillment Officer → Customer (Pickup)'),
                ],
                max_length=24,
            ),
        ),
        migrations.RunPython(rename_stages_forward, rename_stages_reverse),
    ]
