from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0015_admintotp'),
    ]

    operations = [
        migrations.CreateModel(
            name='Promotion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(help_text='Main headline shown to the customer.', max_length=120)),
                ('subtitle', models.CharField(blank=True, help_text='Optional supporting line below the headline.', max_length=240)),
                ('image', models.ImageField(blank=True, help_text='Landscape 1200\u00d7500 recommended for hero; 1200\u00d7250 for strip banners.', null=True, upload_to='promotions/')),
                ('link_url', models.CharField(blank=True, help_text='Where the banner links to. e.g. /products/?category=fashion or a full URL.', max_length=500)),
                ('cta_text', models.CharField(blank=True, default='Shop Now', help_text='Button label. Leave blank to hide the button.', max_length=40)),
                ('placement', models.CharField(choices=[
                    ('home_hero',    'Home — Hero Carousel'),
                    ('home_strip',   'Home — Mid-page Strip'),
                    ('products_top', 'Product List — Top Banner'),
                    ('cart_banner',  'Cart — Upsell Banner'),
                    ('checkout',     'Checkout — Sidebar Note'),
                ], default='home_hero', max_length=20)),
                ('priority', models.PositiveIntegerField(default=0, help_text='Higher priority shows first when multiple promos exist for the same placement.')),
                ('bg_color', models.CharField(blank=True, default='', help_text='Optional background colour (CSS value, e.g. #C9A84C). Used when there is no image.', max_length=20)),
                ('text_color', models.CharField(blank=True, default='', help_text='Optional text colour.', max_length=20)),
                ('starts_at', models.DateTimeField(blank=True, help_text='Leave blank to start immediately.', null=True)),
                ('ends_at', models.DateTimeField(blank=True, help_text='Leave blank for no end date.', null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('impressions', models.PositiveIntegerField(default=0, editable=False)),
                ('clicks', models.PositiveIntegerField(default=0, editable=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-priority', '-created'],
            },
        ),
    ]
