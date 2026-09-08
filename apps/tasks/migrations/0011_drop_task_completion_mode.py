"""Drop Task.completion_mode.

Whether a task was completed individually or as a team no longer changes
anything: a Lead's close and an assignee's own mark are independent of it,
so the field only described a distinction nothing acted on.

TaskLog.completed_as is deliberately left in place — it records how each
past completion was made, and dropping it would discard that history.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0010_rename_taskactivity_task_index"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="task",
            name="completion_mode",
        ),
    ]
