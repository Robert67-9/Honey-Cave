from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0003_otp_userprofile_paymentsettings_paymentref'),
    ]

    operations = [
        # Add GPS to Branch
        migrations.AddField(
            model_name='branch',
            name='latitude',
            field=models.FloatField(blank=True, null=True, help_text='Branch GPS latitude'),
        ),
        migrations.AddField(
            model_name='branch',
            name='longitude',
            field=models.FloatField(blank=True, null=True, help_text='Branch GPS longitude'),
        ),
        # Add location + nearest_branch to UserProfile
        migrations.AddField(
            model_name='userprofile',
            name='latitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='longitude',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='nearest_branch',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_customers',
                to='mall.branch',
            ),
        ),
    ]
