# Generated by Django 2.0.8 on 2018-10-30 12:17

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0023_auto_20181025_1447'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recipienttotals',
            name='recipient_profile',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='totals', to='security.RecipientProfile'),
        ),
    ]