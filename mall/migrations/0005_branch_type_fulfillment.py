from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0004_branch_gps_userprofile_location'),
    ]

    operations = [
        # Branch type
        migrations.AddField(
            model_name='branch',
            name='branch_type',
            field=models.CharField(
                max_length=10,
                choices=[('main','Main Branch'),('express','Express Kiosk'),('agent','Mobile Agent Point')],
                default='main',
            ),
        ),
        # Order fulfillment type
        migrations.AddField(
            model_name='order',
            name='fulfillment_type',
            field=models.CharField(
                max_length=10,
                choices=[('pickup','Branch Pickup'),('delivery','Home Delivery')],
                default='pickup',
            ),
        ),
        # Order delivery address
        migrations.AddField(
            model_name='order',
            name='delivery_address',
            field=models.TextField(
                blank=True,
                help_text='Full address for home delivery',
            ),
        ),
    ]
