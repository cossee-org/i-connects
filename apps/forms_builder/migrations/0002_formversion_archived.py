from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forms_builder", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="formversion",
            name="archived",
            field=models.BooleanField(default=False),
        ),
    ]
