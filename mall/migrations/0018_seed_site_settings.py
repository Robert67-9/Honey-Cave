from django.db import migrations


def seed_settings(apps, schema_editor):
    """Pre-fill SiteSettings with the contact info that used to be hardcoded
    in the contact.html template, so upgrading stores don't lose their data."""
    SiteSettings = apps.get_model('mall', 'SiteSettings')
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={
            'phone_primary':   '+233 59 178 4205',
            'phone_secondary': '+233 50 536 4835',
            'email':           'info@honeycave.com',
            'whatsapp':        '233591784205',
            'hours_weekday':   'Mon–Sat: 8am – 10pm',
            'hours_sunday':    'Sun: 10am – 6pm',
            'head_office':     'North Legon, Accra, Ghana',
        },
    )


def unseed_settings(apps, schema_editor):
    # Reversing just deletes the row; safe either way.
    SiteSettings = apps.get_model('mall', 'SiteSettings')
    SiteSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0017_sitesettings'),
    ]

    operations = [
        migrations.RunPython(seed_settings, unseed_settings),
    ]
