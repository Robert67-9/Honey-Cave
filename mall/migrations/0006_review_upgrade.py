from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mall', '0005_branch_type_fulfillment'),
    ]

    operations = [
        # Add new fields to Review
        migrations.AddField(
            model_name='review',
            name='title',
            field=models.CharField(blank=True, max_length=120, help_text='Short summary headline'),
        ),
        migrations.AddField(
            model_name='review',
            name='order_item',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='review',
                to='mall.orderitem',
                help_text='The specific order item this review is for',
            ),
        ),
        migrations.AddField(
            model_name='review',
            name='is_verified_purchase',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='review',
            name='is_approved',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='review',
            name='helpful_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='review',
            name='updated',
            field=models.DateTimeField(auto_now=True),
        ),
        # Add user related_name
        migrations.AlterField(
            model_name='review',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='reviews',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Unique together: one review per user per product
        migrations.AlterUniqueTogether(
            name='review',
            unique_together={('product', 'user')},
        ),
        # New ReviewHelpful model
        migrations.CreateModel(
            name='ReviewHelpful',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('review', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='helpful_votes', to='mall.review')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={'unique_together': {('review', 'user')}},
        ),
    ]
