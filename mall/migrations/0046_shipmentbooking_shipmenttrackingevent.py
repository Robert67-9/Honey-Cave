# Generated for the Shipping (HCL) import/export booking + tracking feature.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0045_storeapplication'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ShipmentBooking',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tracking_code', models.CharField(db_index=True, max_length=20, unique=True)),
                ('pickup_address', models.CharField(max_length=255)),
                ('pickup_city', models.CharField(max_length=100)),
                ('pickup_country', models.CharField(max_length=100)),
                ('dest_address', models.CharField(max_length=255)),
                ('dest_city', models.CharField(max_length=100)),
                ('dest_country', models.CharField(max_length=100)),
                ('sender_name', models.CharField(max_length=150)),
                ('sender_email', models.EmailField(blank=True, max_length=254)),
                ('sender_phone', models.CharField(max_length=30)),
                ('recipient_name', models.CharField(max_length=150)),
                ('recipient_email', models.EmailField(blank=True, max_length=254)),
                ('recipient_phone', models.CharField(max_length=30)),
                ('package_type', models.CharField(choices=[('document', 'Document / Letter'), ('small_parcel', 'Small Parcel / Box'), ('heavy_box', 'Heavy Cargo Box'), ('fragile', 'Fragile / Sensitive Items')], default='small_parcel', max_length=20)),
                ('weight_kg', models.DecimalField(decimal_places=2, max_digits=6)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('delivery_option', models.CharField(choices=[('standard', 'Honey Cave Standard Ground'), ('express', 'Honey Cave Express Priority'), ('sameday', 'Honey Cave Same-Day Courier'), ('international', 'Honey Cave Global Gateway')], default='standard', max_length=20)),
                ('pickup_date', models.DateField()),
                ('insurance_required', models.BooleanField(default=False)),
                ('base_price', models.DecimalField(decimal_places=2, max_digits=8)),
                ('weight_fee', models.DecimalField(decimal_places=2, max_digits=8)),
                ('speed_multiplier', models.DecimalField(decimal_places=2, max_digits=4)),
                ('insurance_fee', models.DecimalField(decimal_places=2, max_digits=8)),
                ('total_cost', models.DecimalField(decimal_places=2, max_digits=8)),
                ('status', models.CharField(choices=[('picked_up', 'Picked Up'), ('processing', 'Processing'), ('in_transit', 'In Transit'), ('out_for_delivery', 'Out for Delivery'), ('delivered', 'Delivered')], default='picked_up', max_length=20)),
                ('current_location', models.CharField(blank=True, max_length=255)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('booked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='shipment_bookings', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created'],
            },
        ),
        migrations.CreateModel(
            name='ShipmentTrackingEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(help_text="e.g. 'Aug 07, 09:30 AM' or 'Today, 2:00 PM'", max_length=120)),
                ('location', models.CharField(max_length=200)),
                ('description', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('picked_up', 'Picked Up'), ('processing', 'Processing'), ('in_transit', 'In Transit'), ('out_for_delivery', 'Out for Delivery'), ('delivered', 'Delivered')], max_length=20)),
                ('completed', models.BooleanField(default=True)),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('booking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='mall.shipmentbooking')),
            ],
            options={
                'ordering': ['order', 'created'],
            },
        ),
    ]
