from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Branch',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('region', models.CharField(max_length=50, choices=[
                    ('greater_accra','Greater Accra Region'),('ashanti','Ashanti Region'),
                    ('western','Western Region'),('western_north','Western North Region'),
                    ('central','Central Region'),('eastern','Eastern Region'),
                    ('volta','Volta Region'),('oti','Oti Region'),
                    ('bono','Bono Region'),('bono_east','Bono East Region'),
                    ('ahafo','Ahafo Region'),('northern','Northern Region'),
                    ('savannah','Savannah Region'),('north_east','North East Region'),
                    ('upper_east','Upper East Region'),('upper_west','Upper West Region'),
                ])),
                ('name', models.CharField(max_length=200)),
                ('address', models.CharField(max_length=300)),
                ('city', models.CharField(max_length=100)),
                ('phone', models.CharField(blank=True, max_length=30)),
                ('email', models.EmailField(blank=True)),
                ('opening_hours', models.CharField(default='Mon–Sat: 8am – 8pm | Sun: 10am – 6pm', max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('landmark', models.CharField(blank=True, help_text='Nearby landmark for directions', max_length=200)),
            ],
            options={'ordering': ['region', 'name'], 'verbose_name_plural': 'Branches'},
        ),
        migrations.AddField(
            model_name='order',
            name='branch',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='mall.branch'),
        ),
    ]
