from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('prison', '0016_auto_20171121_1110'),
    ]
    operations = [
        migrations.CreateModel(
            name='Region',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
            ],
            options={
                'ordering': ('name',),
            },
        ),
    ]
