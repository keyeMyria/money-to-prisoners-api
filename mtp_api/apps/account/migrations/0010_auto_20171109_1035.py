# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-11-09 10:35
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0009_auto_20160905_1421'),
    ]

    operations = [
        migrations.AlterField(
            model_name='balance',
            name='closing_balance',
            field=models.BigIntegerField(),
        ),
    ]
