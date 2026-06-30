from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0032_otp_hash_at_rest'),
    ]

    operations = [
        # Widen extra_account so it can hold longer values
        migrations.AlterField(
            model_name='paymentsettings',
            name='extra_account',
            field=models.CharField(
                blank=True,
                default='',
                max_length=300,
            ),
        ),
        migrations.AddField(
            model_name='paymentsettings',
            name='private_key',
            field=models.CharField(
                blank=True,
                default='',
                max_length=500,
            ),
        ),
    ]
