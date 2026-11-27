from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0024_remove_tasklog_evidence_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tasklog",
            name="evidence_note",
            field=models.TextField(
                blank=True,
                default="",
                max_length=1000,
                help_text=(
                    "The assignee's one note on their evidence, covering every link "
                    "they gave (``evidence_items``). Shown only to the assignee, the "
                    "task's reviewers and the project's Leads."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="tasklog",
            name="evidence_updated_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "When the assignee last set or changed their evidence (the note "
                    "and the ``evidence_items`` links). Kept when they undo their "
                    "done mark, as the evidence is, so it is still there "
                    "if they mark it done again."
                ),
            ),
        ),
    ]
