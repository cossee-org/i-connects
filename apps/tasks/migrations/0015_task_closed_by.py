import django.db.models.deletion
from django.db import migrations, models


def backfill_from_the_audit_log(apps, schema_editor):
    """Name the closer of tasks that were already completed.

    The fields are new; the fact is not — every close has written a
    ``task.completed`` audit row naming its actor since long before this.
    Read the most recent one per closed task and resolve its actor back to
    a membership on that task's project.

    Anything that does not resolve is left null, which the page reports the
    same way it reports a closer whose membership row has since gone.
    """
    Task = apps.get_model("tasks", "Task")
    AuditLog = apps.get_model("audit", "AuditLog")
    ProjectMember = apps.get_model("projects", "ProjectMember")

    closed = Task.objects.filter(status="COMPLETED", closed_by__isnull=True)
    task_ids = list(closed.values_list("id", flat=True))
    if not task_ids:
        return

    # Oldest first, so the newest row per task is the one left standing.
    closes = {}
    for entry in AuditLog.objects.filter(
        action="task.completed", entity_type="task", entity_id__in=task_ids
    ).order_by("timestamp", "id").values("entity_id", "actor_id", "timestamp"):
        closes[entry["entity_id"]] = (entry["actor_id"], entry["timestamp"])

    for task in closed.only("id", "project_id"):
        actor_id, timestamp = closes.get(task.id, (None, None))
        if actor_id is None:
            continue
        member = (
            ProjectMember.objects.filter(
                project_id=task.project_id, users=actor_id, deleted=False
            )
            .order_by("id")
            .first()
        )
        Task.objects.filter(pk=task.pk).update(
            closed_by=member, closed_at=timestamp
        )


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0006_alter_auditlog_action"),
        ("projects", "0008_project_name_max_length_200"),
        ("tasks", "0014_task_requires_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="closed_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "When the task was marked completed. Cleared on reopening, "
                    "so it always describes the completion the task currently "
                    "stands on."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Who marked this task completed — the Lead who closed it, "
                    "or the reviewer whose sign-off was the last one "
                    "outstanding. Kept as written even after they leave the "
                    "task, and cleared when the task is reopened. Nulled only "
                    "if their membership row is deleted outright, which the "
                    "page reports as a former member."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="closed_tasks",
                to="projects.projectmember",
            ),
        ),
        migrations.RunPython(
            backfill_from_the_audit_log, migrations.RunPython.noop, elidable=True
        ),
    ]
