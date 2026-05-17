# Generated manually — increase social_avatar_url max_length to 500

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_merge_accounts_0007_heads'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profile',
            name='social_avatar_url',
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
