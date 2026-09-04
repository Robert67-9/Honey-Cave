# Generated manually to add SiteSettings.maintenance_eta

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0043_sitesettings_nalo_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='maintenance_eta',
            field=models.DateTimeField(blank=True, null=True, help_text='Optional. If set, the maintenance page shows a live countdown to this time instead of a generic message.'),
        ),
    ]
