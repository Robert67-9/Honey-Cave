from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0021_userprofile_google_oauth'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='branch',
            name='storekeeper',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='managed_branches',
                to=settings.AUTH_USER_MODEL,
                help_text='Storekeeper account assigned to this branch — can confirm orders and hand off to riders/customers.',
            ),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='is_storekeeper',
            field=models.BooleanField(
                default=False,
                help_text='Marks this user as a branch storekeeper. They can log in to the storekeeper portal and process orders for the branch they manage.',
            ),
        ),
        migrations.CreateModel(
            name='HandoffCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('stage', models.CharField(choices=[
                    ('admin_to_keeper',   'Admin → Storekeeper'),
                    ('keeper_to_rider',   'Storekeeper → Rider'),
                    ('rider_to_customer', 'Rider → Customer'),
                    ('keeper_to_customer','Storekeeper → Customer (Pickup)'),
                ], max_length=24)),
                ('code', models.CharField(db_index=True, help_text='6-digit numeric code shown to the issuer and entered by the receiver.', max_length=6)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('locked', models.BooleanField(default=False, help_text='Set to True after MAX_ATTEMPTS wrong tries. Requires admin unlock.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('issued_to_label', models.CharField(blank=True, default='', help_text='Free-text label for who the code was sent to (e.g. rider name, customer name) — useful when no User account exists.', max_length=120)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='handoff_codes', to='mall.order')),
                ('used_by', models.ForeignKey(blank=True, help_text='User who entered the correct code (if logged in).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='handoff_codes_used', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['order', 'stage'], name='mall_handof_order_i_a8f1d2_idx')],
            },
        ),
    ]
