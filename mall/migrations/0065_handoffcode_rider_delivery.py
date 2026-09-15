from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0064_pushsubscription'),
    ]

    operations = [
        migrations.AddField(
            model_name='handoffcode',
            name='rider_delivery',
            field=models.ForeignKey(
                to='mall.RiderDelivery',
                on_delete=django.db.models.deletion.CASCADE,
                null=True, blank=True,
                related_name='handoff_codes',
                help_text=(
                    "Which seller's delivery this code belongs to. Null for "
                    "admin_to_officer (whole-order, before sellers are split) "
                    "and officer_to_customer (pickup orders have no rider "
                    "split)."
                ),
            ),
        ),
    ]
