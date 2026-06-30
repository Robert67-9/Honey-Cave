from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0020_merge_20260422_1637'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='google_id',
            field=models.CharField(blank=True, db_index=True, default='', help_text='Stable Google account ID — set when user signs in with Google.', max_length=64),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='email_verified',
            field=models.BooleanField(default=False, help_text='True when the email address has been verified (via Google OAuth or OTP).'),
        ),
    ]
