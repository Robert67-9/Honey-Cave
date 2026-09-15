from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0065_handoffcode_rider_delivery'),
    ]

    operations = [
        migrations.AddField(
            model_name='wallettransaction',
            name='rider_delivery',
            field=models.ForeignKey(
                to='mall.RiderDelivery',
                on_delete=django.db.models.deletion.SET_NULL,
                null=True, blank=True,
                related_name='wallet_transactions',
                help_text=(
                    "Set when this credit is scoped to one seller's delivery "
                    "within a multi-seller order, rather than the whole order."
                ),
            ),
        ),
    ]
