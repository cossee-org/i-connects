"""Evidence is now called attachments: rename the model and the TaskLog
fields in place, keeping every row."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0027_remove_taskevidence_note"),
    ]

    operations = [
        migrations.RenameModel(old_name="TaskEvidence", new_name="TaskAttachment"),
        migrations.RenameField(
            model_name="tasklog", old_name="evidence_note", new_name="attachment_note"
        ),
        migrations.RenameField(
            model_name="tasklog",
            old_name="evidence_updated_at",
            new_name="attachments_updated_at",
        ),
        migrations.RenameIndex(
            model_name="taskattachment",
            new_name="tasks_taska_task_lo_dfd8cb_idx",
            old_name="tasks_taske_task_lo_44624f_idx",
        ),
        migrations.AlterField(
            model_name="taskattachment",
            name="task_log",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attachments",
                to="tasks.tasklog",
            ),
        ),
        migrations.AlterField(
            model_name="tasklog",
            name="attachment_note",
            field=models.TextField(
                blank=True,
                default="",
                help_text="The assignee's one note on their attachments, covering every link they gave (``attachments``). Shown only to the assignee, the task's reviewers and the project's Leads.",
                max_length=1000,
            ),
        ),
        migrations.AlterField(
            model_name="tasklog",
            name="attachments_updated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the assignee last set or changed their attachments (the note and the ``attachments`` links). Kept when they undo their done mark, as the attachments are, so they are still there if they mark it done again.",
                null=True,
            ),
        ),
    ]
