# Generated by Django 2.0.8 on 2018-10-03 10:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prison', '0016_auto_20171121_1110'),
        ('security', '0017_auto_20180914_1613'),
    ]

    operations = [
        migrations.AddField(
            model_name='prisonerprofile',
            name='disbursement_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='prisonerprofile',
            name='disbursement_total',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='prisonerprofile',
            name='recipients',
            field=models.ManyToManyField(related_name='prisoners', to='security.RecipientProfile'),
        ),
        migrations.AddField(
            model_name='recipientprofile',
            name='prisons',
            field=models.ManyToManyField(related_name='recipients', to='prison.Prison'),
        ),
        migrations.AlterField(
            model_name='prisonerprofile',
            name='prisoner_dob',
            field=models.DateField(blank=True, null=True),
        ),
    ]