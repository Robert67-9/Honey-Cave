# Generated for the "Own a Store" applicant flow.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0044_sitesettings_maintenance_eta'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StoreApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('store_name', models.CharField(max_length=200)),
                ('business_reg_no', models.CharField(max_length=100, verbose_name='Business Registration No.')),
                ('location', models.CharField(help_text='City / area where the store operates', max_length=300)),
                ('product_category', models.CharField(max_length=150)),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('id_document', models.FileField(help_text='National ID, passport, or business registration certificate', upload_to='store_applications/ids/')),
                ('status', models.CharField(choices=[('pending', 'Pending Review'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', max_length=10)),
                ('decision_note', models.CharField(blank=True, default='', max_length=300)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('applicant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='store_applications', to=settings.AUTH_USER_MODEL)),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='decided_store_applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created'],
            },
        ),
    ]
