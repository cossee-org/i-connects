"""Align the TaskActivity index name with what the model declares.

Pre-existing drift, unrelated to any behaviour: migration 0008 created the
index under a hand-written name while the model leaves it to Django's
default. Renaming it here keeps `makemigrations --check` quiet.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0009_task_is_priority"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="taskactivity",
            new_name="tasks_taska_task_id_aa06a9_idx",
            old_name="tasks_tacti_task_id_idx",
        ),
    ]
