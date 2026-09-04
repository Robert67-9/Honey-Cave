# Generated manually — reconciles migration state with models.py after
# standardizing BranchProduct.product's related_name on 'branch_pricing'
# (0049 had drifted it to 'branch_products', causing the officer portal /
# public product page split. See mall/models.py:489.)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0050_nalo_auth_key_and_id_fix'),
    ]

    operations = [
        migrations.AlterField(
            model_name='branchproduct',
            name='product',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='branch_pricing', to='mall.product'),
        ),
    ]
