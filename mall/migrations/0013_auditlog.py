from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0012_rider_delivery'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(
                    choices=[
                        ('product_create', 'Product Created'), ('product_update', 'Product Updated'),
                        ('product_delete', 'Product Deleted'), ('category_create', 'Category Created'),
                        ('category_update', 'Category Updated'), ('category_delete', 'Category Deleted'),
                        ('order_status', 'Order Status Changed'), ('order_note', 'Order Note Added'),
                        ('order_delete', 'Order Deleted'), ('rider_assign', 'Rider Assigned'),
                        ('user_staff', 'User Staff Status Toggled'), ('user_active', 'User Active Status Toggled'),
                        ('user_superuser', 'User Superuser Status Toggled'), ('user_delete', 'User Deleted'),
                        ('branch_create', 'Branch Created'), ('branch_update', 'Branch Updated'),
                        ('branch_delete', 'Branch Deleted'), ('promo_create', 'Promo Code Created'),
                        ('promo_update', 'Promo Code Updated'), ('promo_delete', 'Promo Code Deleted'),
                        ('review_toggle', 'Review Visibility Toggled'), ('review_delete', 'Review Deleted'),
                        ('payment_create', 'Payment Method Created'), ('payment_update', 'Payment Method Updated'),
                        ('payment_delete', 'Payment Method Deleted'), ('csv_import', 'CSV Import'),
                        ('admin_login', 'Admin Login'), ('admin_logout', 'Admin Logout'),
                    ],
                    db_index=True, max_length=40,
                )),
                ('target_repr', models.CharField(blank=True, max_length=300)),
                ('detail', models.TextField(blank=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='audit_logs',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Audit Log Entry',
                'verbose_name_plural': 'Audit Log',
                'ordering': ['-timestamp'],
                'default_permissions': ('view',),
            },
        ),
    ]
