# Generated for the Air / Sea shipping method addition.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0046_shipmentbooking_shipmenttrackingevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='shipmentbooking',
            name='shipping_method',
            field=models.CharField(choices=[('air', 'Air Freight'), ('sea', 'Sea Freight')], default='air', max_length=10),
        ),
        migrations.AlterField(
            model_name='shipmentbooking',
            name='delivery_option',
            field=models.CharField(choices=[('standard', 'Honey Cave Standard Ground'), ('express', 'Honey Cave Express Priority'), ('sameday', 'Honey Cave Same-Day Courier'), ('international', 'Honey Cave Global Gateway'), ('sea_economy', 'Honey Cave Sea Economy'), ('sea_priority', 'Honey Cave Sea Priority')], default='standard', max_length=20),
        ),
    ]
