# Hand-written (not generated in-container — see project note on the
# web service having no source volume mount).
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import datetime


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0067_rider_email_alter_handoffcode_rider_delivery_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RiderAutoLoginToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, editable=False, max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('rider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='auto_login_tokens', to='mall.rider')),
            ],
            options={
                'verbose_name': 'Rider Auto-Login Token',
            },
        ),
    ]
