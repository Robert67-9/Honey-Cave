# Originally added Moolre-specific fields. Moolre was later removed, but
# extra_account/private_key are still used by Paystack (extra_account stores
# "username::account_number"), so the AddField operations are kept here —
# only the Moolre-specific choices/logic were stripped elsewhere.
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('mall', '0024_branchproduct'),
    ]
    operations = [
        migrations.AddField(
            model_name='paymentsettings',
            name='extra_account',
            field=models.CharField(
                blank=True,
                default='',
                max_length=300,
                help_text='Leave blank for Paystack.',
            ),
        ),
    ]
