from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0026_merge_evidence_notes"),
    ]

    operations = [
        migrations.RemoveField(model_name="taskevidence", name="note"),
        migrations.AlterField(
            model_name="taskevidence",
            name="url",
            field=models.URLField(
                max_length=500,
                help_text="A link to the work (a document, a folder, a pull request).",
            ),
        ),
        migrations.AlterField(
            model_name="taskevidence",
            name="position",
            field=models.PositiveSmallIntegerField(
                help_text="Where the assignee put this link in their list, from 0."
            ),
        ),
    ]
