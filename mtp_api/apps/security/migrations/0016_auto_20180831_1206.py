# Generated by Django 2.0.8 on 2018-08-31 11:06

from django.db import migrations, models
import django.db.models.deletion

from security.constants import TIME_PERIOD


def create_profile_totals(apps, schema_editor):
    SenderProfile = apps.get_model('security', 'SenderProfile')
    PrisonerProfile = apps.get_model('security', 'PrisonerProfile')
    SenderTotals = apps.get_model('security', 'SenderTotals')
    PrisonerTotals = apps.get_model('security', 'PrisonerTotals')

    sender_profiles = []
    prisoner_profiles = []
    for time_period in TIME_PERIOD:
        for sender_profile in SenderProfile.objects.all():
            sender_profiles.append(SenderTotals(
                sender_profile=sender_profile, time_period=time_period
            ))
        for prisoner_profile in PrisonerProfile.objects.all():
            prisoner_profiles.append(PrisonerTotals(
                prisoner_profile=prisoner_profile, time_period=time_period
            ))

    SenderProfile.objects.bulk_create(sender_profiles)
    PrisonerProfile.objects.bulk_create(prisoner_profiles)


class Migration(migrations.Migration):

    dependencies = [
        ('security', '0015_simple_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrisonerTotals',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('credit_count', models.IntegerField(default=0)),
                ('credit_total', models.IntegerField(default=0)),
                ('sender_count', models.IntegerField(default=0)),
                ('time_period', models.CharField(choices=[('all_time', 'All time'), ('last_7_days', 'Last 7 days'), ('last_30_days', 'Last 30 days'), ('last_6_months', 'Last 6 months')], max_length=50)),
            ],
        ),
        migrations.CreateModel(
            name='SenderTotals',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('credit_count', models.IntegerField(default=0)),
                ('credit_total', models.IntegerField(default=0)),
                ('prison_count', models.IntegerField(default=0)),
                ('prisoner_count', models.IntegerField(default=0)),
                ('time_period', models.CharField(choices=[('all_time', 'All time'), ('last_7_days', 'Last 7 days'), ('last_30_days', 'Last 30 days'), ('last_6_months', 'Last 6 months')], max_length=50)),
            ],
        ),
        migrations.RemoveField(
            model_name='prisonerprofile',
            name='credit_count',
        ),
        migrations.RemoveField(
            model_name='prisonerprofile',
            name='credit_total',
        ),
        migrations.RemoveField(
            model_name='senderprofile',
            name='credit_count',
        ),
        migrations.RemoveField(
            model_name='senderprofile',
            name='credit_total',
        ),
        migrations.AddField(
            model_name='sendertotals',
            name='sender_profile',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='security.SenderProfile'),
        ),
        migrations.AddField(
            model_name='prisonertotals',
            name='prisoner_profile',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='security.PrisonerProfile'),
        ),
        migrations.RunPython(create_profile_totals),
    ]
