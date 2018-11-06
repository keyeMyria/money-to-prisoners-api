# Generated by Django 2.0.8 on 2018-11-01 14:05

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import notification.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('notification', '0004_auto_20181101_1158'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='event',
            name='subscription',
        ),
        migrations.AddField(
            model_name='event',
            name='description',
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name='event',
            name='rule',
            field=models.CharField(max_length=8, validators=[notification.models.validate_rule_code]),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='event',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
    ]