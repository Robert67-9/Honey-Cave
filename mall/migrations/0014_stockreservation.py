from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0013_auditlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='StockReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(db_index=True, max_length=40)),
                ('quantity', models.PositiveIntegerField()),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reservations',
                    to='mall.product',
                )),
            ],
            options={
                'unique_together': {('session_key', 'product')},
            },
        ),
    ]
