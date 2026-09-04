import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mall', '0051_branchproduct_related_name_pricing'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfficerAutoLoginToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, editable=False, max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('officer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='auto_login_tokens', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Officer Auto-Login Token',
            },
        ),
    ]
