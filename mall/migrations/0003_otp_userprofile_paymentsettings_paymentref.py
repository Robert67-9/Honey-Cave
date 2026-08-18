from django.db import migrations, models
import django.db.models.deletion
import mall.models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0002_branch_order_branch'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone', models.CharField(blank=True, max_length=20)),
                ('is_verified', models.BooleanField(default=False)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profile', to='auth.user')),
            ],
        ),
        migrations.CreateModel(
            name='OTPVerification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(default=mall.models.generate_otp, max_length=6)),
                ('purpose', models.CharField(choices=[('signup', 'Sign Up Verification'), ('password_reset', 'Password Reset')], default='signup', max_length=20)),
                ('is_used', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='otps', to='auth.user')),
            ],
        ),
        migrations.CreateModel(
            name='PaymentSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('momo', 'MTN Mobile Money'), ('vodafone', 'Vodafone Cash'), ('airteltigo', 'AirtelTigo Money'), ('bank', 'Bank Transfer'), ('other', 'Other')], default='momo', max_length=20)),
                ('account_name', models.CharField(max_length=200)),
                ('account_number', models.CharField(max_length=50)),
                ('instructions', models.TextField(blank=True, help_text='Payment instructions shown to customers')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name_plural': 'Payment Settings',
            },
        ),
        migrations.AddField(
            model_name='order',
            name='payment_reference',
            field=models.CharField(blank=True, help_text='Mobile money / bank transaction reference', max_length=100),
        ),
    ]
