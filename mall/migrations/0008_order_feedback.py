from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0007_features'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nps_score', models.PositiveSmallIntegerField(
                    choices=[(i, str(i)) for i in range(0, 11)],
                    help_text='0 = Not at all likely, 10 = Extremely likely to recommend'
                )),
                ('delivery_rating',  models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])),
                ('packaging_rating', models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])),
                ('service_rating',   models.PositiveSmallIntegerField(choices=[(i, i) for i in range(1, 6)])),
                ('comment', models.TextField(blank=True)),
                ('photo_1', models.ImageField(blank=True, null=True, upload_to='feedback/')),
                ('photo_2', models.ImageField(blank=True, null=True, upload_to='feedback/')),
                ('photo_3', models.ImageField(blank=True, null=True, upload_to='feedback/')),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('order', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='feedback',
                    to='mall.order',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='feedback',
                    to='auth.user',
                )),
            ],
            options={'ordering': ['-created']},
        ),
    ]
