from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0008_order_feedback'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentsettings',
            name='icon',
            field=models.CharField(blank=True, default='', help_text='Emoji or icon (auto-filled based on provider)', max_length=50),
        ),
    ]
