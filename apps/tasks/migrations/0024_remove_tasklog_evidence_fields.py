from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0023_move_evidence_to_items"),
    ]

    operations = [
        migrations.RemoveField(model_name="tasklog", name="evidence_url"),
        migrations.RemoveField(model_name="tasklog", name="evidence_note"),
        migrations.AlterField(
            model_name="tasklog",
            name="evidence_updated_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "When the assignee last set or changed their evidence (the "
                    "``evidence_items`` list). Kept when they undo their done mark, "
                    "as the items are, so the evidence is still there "
                    "if they mark it done again."
                ),
            ),
        ),
    ]
