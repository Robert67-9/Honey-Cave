import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0011_paymentsettings_paystack'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending',    'Pending'),
                    ('processing', 'Processing'),
                    ('shipped',    'Shipped'),
                    ('dispatched', 'Dispatched'),
                    ('delivered',  'Delivered'),
                    ('confirmed',  'Customer Confirmed'),
                    ('cancelled',  'Cancelled'),
                ],
                default='pending',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='notif_type',
            field=models.CharField(
                choices=[
                    ('new_order',        'New Order Placed'),
                    ('order_update',     'Order Status Updated'),
                    ('stock_alert',      'Low Stock Alert'),
                    ('order_cancel',     'Order Cancelled'),
                    ('rider_dispatched', 'Rider Dispatched'),
                    ('delivery_done',    'Delivery Completed'),
                    ('delivery_confirm', 'Customer Confirmed Delivery'),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name='RiderDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rider_name',    models.CharField(max_length=150)),
                ('rider_phone',   models.CharField(max_length=20)),
                ('token',         models.CharField(editable=False, max_length=64, unique=True)),
                ('dispatched_at', models.DateTimeField(auto_now_add=True)),
                ('delivered_at',  models.DateTimeField(blank=True, null=True)),
                ('confirmed_at',  models.DateTimeField(blank=True, null=True)),
                ('rider_note',    models.TextField(blank=True)),
                ('order', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='rider_delivery',
                    to='mall.order',
                )),
            ],
            options={'verbose_name': 'Rider Delivery'},
        ),
    ]
