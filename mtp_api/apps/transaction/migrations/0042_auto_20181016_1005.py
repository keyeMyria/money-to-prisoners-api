# Generated by Django 2.0.8 on 2018-10-16 09:05

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transaction', '0041_add_indices'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='amount',
            field=models.BigIntegerField(),
        ),
    ]