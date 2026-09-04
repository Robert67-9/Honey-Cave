from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0015_admintotp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='payment_reference',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Paystack/bank transfer reference',
                max_length=100,
            ),
        ),
    ]
