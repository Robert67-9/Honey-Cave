from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0033_paymentsettings_private_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='maintenance_mode',
            field=models.BooleanField(
                default=False,
                help_text='When ON, all visitors (except staff) see the maintenance page.',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='maintenance_message',
            field=models.TextField(
                blank=True,
                default='We are performing scheduled maintenance. We will be back shortly!',
                help_text='Message shown on the maintenance page.',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='maintenance_bypass_token',
            field=models.CharField(
                blank=True,
                default='',
                max_length=80,
                help_text='Optional secret token. Visitors who add ?bypass=TOKEN to any URL can preview the site.',
            ),
        ),
    ]
