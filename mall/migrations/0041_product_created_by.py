from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mall', '0040_userprofile_assigned_products'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                help_text='User (admin or officer) who added this product.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='uploaded_products',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
