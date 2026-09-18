from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Re-creates the migration record for a field that was already applied
    directly to the database in an earlier session, but whose migration
    file was lost before being committed to git. Uses SeparateDatabaseAndState
    so Django's ORM state catches up WITHOUT re-running ALTER TABLE against
    a column that already exists in the live database.
    """

    dependencies = [
        ('mall', '0068_riderautologintoken'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='order',
                    name='delivery_digital_address',
                    field=models.CharField(blank=True, default='', help_text='Ghana Post GPS digital address, e.g. GA-184-9021 — auto-filled from the location pin when available.', max_length=20),
                ),
            ],
            database_operations=[],
        ),
    ]
