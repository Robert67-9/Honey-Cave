from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0016_promotion'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_primary', models.CharField(blank=True, default='', help_text='Main customer phone, e.g. +233 59 178 4205', max_length=40)),
                ('phone_secondary', models.CharField(blank=True, default='', help_text='Optional second phone number.', max_length=40)),
                ('email', models.EmailField(blank=True, default='', help_text='Customer support email address.', max_length=254)),
                ('whatsapp', models.CharField(blank=True, default='', help_text='WhatsApp number. Include country code, no plus sign, e.g. 233591784205', max_length=40)),
                ('hours_weekday', models.CharField(blank=True, default='Mon–Sat: 8am – 10pm', help_text='Weekday opening hours shown on contact page.', max_length=100)),
                ('hours_sunday', models.CharField(blank=True, default='Sun: 10am – 6pm', help_text='Sunday opening hours.', max_length=100)),
                ('head_office', models.CharField(blank=True, default='Accra, Ghana', help_text='Head office address shown on contact page.', max_length=200)),
                ('facebook_url', models.URLField(blank=True, default='')),
                ('instagram_url', models.URLField(blank=True, default='')),
                ('twitter_url', models.URLField(blank=True, default='')),
                ('tiktok_url', models.URLField(blank=True, default='')),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Site Settings',
                'verbose_name_plural': 'Site Settings',
            },
        ),
    ]
