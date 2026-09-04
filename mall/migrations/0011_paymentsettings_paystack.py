from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0010_alter_review_options_alter_notification_link_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='paymentsettings',
            name='provider',
            field=models.CharField(
                choices=[('paystack', 'Paystack')],
                default='paystack',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='account_number',
            field=models.CharField(
                max_length=100,
                help_text='Paystack Public Key (pk_live_... or pk_test_...)',
            ),
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='account_name',
            field=models.CharField(
                max_length=200,
                help_text='Display name shown at checkout (e.g. Market Payments)',
            ),
        ),
        migrations.AlterField(
            model_name='paymentsettings',
            name='icon',
            field=models.CharField(
                blank=True,
                default='',
                max_length=50,
                help_text='Emoji or icon (leave blank for default 💳)',
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='payment_reference',
            field=models.CharField(
                blank=True,
                max_length=100,
                help_text='Paystack transaction reference',
            ),
        ),
    ]
