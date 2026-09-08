"""Order comments newest-first.

The ordering change is what this migration is for; the RenameIndex beside it
is pre-existing drift that ``makemigrations`` emits for anyone who runs it,
because 0003 named the index by hand and Django wants its own hashed name.
Carrying it here keeps ``makemigrations --check`` quiet, the same reason
tasks/0010 exists.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("comments", "0003_add_task_fk_make_task_log_nullable"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="comment",
            options={"ordering": ["-created_at"]},
        ),
        migrations.RenameIndex(
            model_name="comment",
            new_name="comments_co_task_id_54a67f_idx",
            old_name="comments_co_task_id_idx",
        ),
    ]
