from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0006_review_upgrade'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        # FEAT-01: Wishlist
        migrations.CreateModel(
            name='WishlistItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('added', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wishlisted_by', to='mall.product')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wishlist', to='auth.user')),
            ],
            options={'ordering': ['-added'], 'unique_together': {('user', 'product')}},
        ),
        # FEAT-06: Product Image Gallery
        migrations.CreateModel(
            name='ProductImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(upload_to='products/gallery/')),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('alt_text', models.CharField(blank=True, max_length=200)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='gallery', to='mall.product')),
            ],
            options={'ordering': ['sort_order', 'created']},
        ),
        # FEAT-07: Promo Codes
        migrations.CreateModel(
            name='PromoCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=50, unique=True)),
                ('discount_type', models.CharField(choices=[('percent', 'Percentage (%)'), ('fixed', 'Fixed Amount (GH₵)')], default='percent', max_length=10)),
                ('discount_value', models.DecimalField(decimal_places=2, max_digits=8)),
                ('min_order_value', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=8)),
                ('max_uses', models.PositiveIntegerField(blank=True, null=True)),
                ('times_used', models.PositiveIntegerField(default=0)),
                ('valid_from', models.DateTimeField(default=django.utils.timezone.now)),
                ('valid_until', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        # FEAT-07: Promo fields on Order
        migrations.AddField(
            model_name='order',
            name='promo_code',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='mall.promocode'),
        ),
        migrations.AddField(
            model_name='order',
            name='discount_amount',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=8),
        ),
        # FEAT-08: Order Notes
        migrations.CreateModel(
            name='OrderNote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.TextField()),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notes', to='mall.order')),
                ('staff', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_notes', to='auth.user')),
            ],
            options={'ordering': ['-created']},
        ),
        # FEAT-NOTIF: Notifications
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('notif_type', models.CharField(choices=[('new_order', 'New Order Placed'), ('order_update', 'Order Status Updated'), ('stock_alert', 'Low Stock Alert'), ('order_cancel', 'Order Cancelled')], max_length=20)),
                ('title', models.CharField(max_length=200)),
                ('message', models.TextField()),
                ('link', models.CharField(blank=True, max_length=300)),
                ('is_read', models.BooleanField(default=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to='auth.user')),
            ],
            options={'ordering': ['-created']},
        ),
    ]
