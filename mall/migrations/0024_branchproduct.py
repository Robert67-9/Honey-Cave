from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0023_sitesettings_logo'),
    ]

    operations = [
        migrations.CreateModel(
            name='BranchProduct',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('price', models.DecimalField(decimal_places=2, max_digits=10,
                                              help_text='Price at this branch (overrides Product.price for this branch only).')),
                ('stock', models.PositiveIntegerField(default=0,
                                                     help_text='Stock available at this branch.')),
                ('is_available', models.BooleanField(default=True,
                                                    help_text='Untick to temporarily hide this product at this branch without deleting the row.')),
                ('updated', models.DateTimeField(auto_now=True)),
                ('branch', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stocked_products',
                    to='mall.branch',
                )),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='branch_pricing',
                    to='mall.product',
                )),
            ],
            options={
                'verbose_name': 'Branch Product',
                'verbose_name_plural': 'Branch Products',
                'ordering': ['branch__name', 'product__name'],
                'indexes': [models.Index(fields=['branch', 'is_available'], name='mall_branch_branch_idx')],
                'unique_together': {('product', 'branch')},
            },
        ),
    ]
