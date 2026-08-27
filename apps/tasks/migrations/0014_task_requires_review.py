from django.db import migrations, models


def backfill_from_reviewers(apps, schema_editor):
    """A task that already has reviewers was already asking for review.

    The flag is new; the behaviour it names is not — until now the review
    rules keyed off the reviewer set alone. Setting it wherever reviewers
    exist keeps every existing task doing exactly what it did before.
    """
    Task = apps.get_model("tasks", "Task")
    reviewed = Task.objects.filter(reviewers__isnull=False).values_list(
        "pk", flat=True
    )
    Task.objects.filter(pk__in=list(reviewed)).update(requires_review=True)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0013_tasklog_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="requires_review",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "When set, each assignee's submission has to be signed off "
                    "by one of the task's reviewers before the task counts as "
                    "done. The form keeps this in step with ``reviewers``, "
                    "which is what the review rules actually read — see "
                    "``needs_review``."
                ),
            ),
        ),
        migrations.RunPython(
            backfill_from_reviewers, migrations.RunPython.noop, elidable=True
        ),
    ]
