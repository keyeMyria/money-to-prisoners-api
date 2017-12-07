# Generated by Django 1.10.8 on 2017-11-29 16:36
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('service', '0002_downtime_user_message'),
    ]
    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target', models.CharField(choices=[('cashbook_login', 'Cashbook: before login'), ('cashbook_all', 'Cashbook: all apps'), ('cashbook_cashbook', 'Cashbook: cashbook app'), ('cashbook_disbursements', 'Cashbook: disbursements app')], max_length=30)),
                ('level', models.SmallIntegerField(choices=[(20, 'Info'), (25, 'Success'), (30, 'Warning'), (40, 'Error')])),
                ('start', models.DateTimeField(default=django.utils.timezone.now)),
                ('end', models.DateTimeField(blank=True, null=True)),
                ('headline', models.CharField(max_length=200)),
                ('message', models.TextField(blank=True)),
                ('public', models.BooleanField(default=False, help_text='Notifications must be public to be seen before login')),
            ],
            options={
                'ordering': ('-end', '-start'),
            },
        ),
    ]