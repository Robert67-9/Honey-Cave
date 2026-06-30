from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0022_handoff_codes'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='logo',
            field=models.ImageField(
                blank=True, null=True, upload_to='branding/',
                help_text='Square logo (recommended 512×512 PNG with transparent background). Shown in the navbar and search engine results. Falls back to /static/images/logo.png if blank.',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='og_image',
            field=models.ImageField(
                blank=True, null=True, upload_to='branding/',
                help_text='Social share preview banner (recommended 1200×630 JPG/PNG). Shown when your site link is pasted on WhatsApp/Facebook/Twitter. Falls back to /static/images/og-image.jpg if blank.',
            ),
        ),
    ]
