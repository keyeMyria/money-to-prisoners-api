# Generated by Django 1.9.12 on 2017-02-13 10:50
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('payment', '0010_auto_20161117_1801'),
    ]
    operations = [
        migrations.AddField(
            model_name='payment',
            name='ip_address',
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
    ]
