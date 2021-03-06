# Generated by Django 1.9.12 on 2017-02-17 16:34
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('security', '0006_auto_20170209_1234'),
    ]
    operations = [
        migrations.CreateModel(
            name='PrisonerRecipientName',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=250)),
                ('prisoner', models.ForeignKey(
                     on_delete=django.db.models.deletion.CASCADE, related_name='recipient_names',
                     related_query_name='recipient_name', to='security.PrisonerProfile'),
                 ),
            ],
        ),
    ]
