from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0018_seed_site_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='wa_enabled',
            field=models.BooleanField(default=False, help_text='Master switch. Turn on to send WhatsApp alerts.'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_phone_number_id',
            field=models.CharField(blank=True, default='', help_text='From Meta dashboard \u2192 WhatsApp \u2192 API Setup \u2192 "Phone number ID".', max_length=80),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_access_token',
            field=models.CharField(blank=True, default='', help_text='Meta access token. Use a permanent system-user token for production.', max_length=300),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_admin_number',
            field=models.CharField(blank=True, default='', help_text='Admin WhatsApp number that gets new-order alerts. Digits only with country code, e.g. 233591784205', max_length=40),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_template_new_order',
            field=models.CharField(blank=True, default='order_confirmation', help_text='Meta template name sent to the CUSTOMER on order placement. Must be pre-approved in Meta Business.', max_length=80),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_template_status',
            field=models.CharField(blank=True, default='order_status_update', help_text='Template sent to the customer when admin changes order status.', max_length=80),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_notify_customer',
            field=models.BooleanField(default=True, help_text='Send WhatsApp to customers on new orders and status changes.'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='wa_notify_admin',
            field=models.BooleanField(default=True, help_text='Send WhatsApp alert to admin number on every new order.'),
        ),
    ]
