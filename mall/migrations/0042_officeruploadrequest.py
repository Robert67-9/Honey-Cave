from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mall', '0041_product_created_by'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfficerUploadRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending admin review'), ('payment_required', 'Payment required'), ('paid', 'Paid — access granted'), ('approved_free', 'Approved free — access granted'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('amount', models.DecimalField(blank=True, decimal_places=2, help_text='Price (GH₵) the admin set for upload access. Only for the pay-first path.', max_digits=10, null=True)),
                ('payment_reference', models.CharField(blank=True, default='', max_length=120)),
                ('amount_paid', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('admin_note', models.CharField(blank=True, default='', max_length=300)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decided_upload_requests', to=settings.AUTH_USER_MODEL)),
                ('officer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upload_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created']},
        ),
    ]
