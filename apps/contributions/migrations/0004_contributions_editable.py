import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def drop_duplicate_credit(apps, schema_editor):
    """Collapse any (task, user) duplicates before the constraint lands.

    The old constraint was per submission, so a user standing behind two of
    a task's assignees — their own membership and a team they are in —
    could hold two rows for one task. The new one says a person is credited
    once per task; keep their earliest row and drop the rest.
    """
    ContributionRecord = apps.get_model("contributions", "ContributionRecord")
    seen = set()
    doomed = []
    for pk, task_id, user_id in ContributionRecord.objects.order_by(
        "id"
    ).values_list("id", "task_id", "user_id"):
        key = (task_id, user_id)
        if key in seen:
            doomed.append(pk)
        else:
            seen.add(key)
    if doomed:
        ContributionRecord.objects.filter(pk__in=doomed).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contributions", "0003_contributionrecord_updated_at_and_more"),
        ("projects", "0008_project_name_max_length_200"),
        ("tasks", "0013_tasklog_review"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="contributionrecord",
            name="contribution_unique_per_tasklog_user",
        ),
        migrations.AddField(
            model_name="contributionrecord",
            name="added_by_hand",
            field=models.BooleanField(
                default=False,
                help_text="True when a lead entered this record themselves rather than it being written by a task closing.",
            ),
        ),
        migrations.AlterField(
            model_name="contributionrecord",
            name="task_log",
            field=models.ForeignKey(
                blank=True,
                help_text="The submission this credit was earned on. Null on a record a lead entered by hand, which stands on their say-so rather than on a mark someone made.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contributions",
                to="tasks.tasklog",
            ),
        ),
        migrations.RunPython(
            drop_duplicate_credit, migrations.RunPython.noop, elidable=True
        ),
        migrations.AddConstraint(
            model_name="contributionrecord",
            constraint=models.UniqueConstraint(
                fields=("task", "user"), name="contribution_unique_per_task_user"
            ),
        ),
    ]
