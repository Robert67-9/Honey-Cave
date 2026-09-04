from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0042_officeruploadrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='nalo_enabled',
            field=models.BooleanField(default=False, help_text='Master switch for Nalo SMS messaging (OTP + alerts).'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='nalo_username',
            field=models.CharField(blank=True, default='', help_text='Nalo SMS API username from your Nalo dashboard.', max_length=150),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='nalo_password',
            field=models.CharField(blank=True, default='', help_text='Nalo SMS API password / auth key from your Nalo dashboard.', max_length=300),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='nalo_api_url',
            field=models.CharField(blank=True, default='https://sms.nalosolutions.com/smsbackend/clientapi/Resl_Nalo/send-message/', help_text='Nalo SMS endpoint. Leave as default unless Nalo gives you a different one.', max_length=300),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='nalo_sender_id',
            field=models.CharField(blank=True, default='', help_text='Approved sender ID shown to recipients (max 11 characters), e.g. HoneyCave.', max_length=11),
        ),
    ]
