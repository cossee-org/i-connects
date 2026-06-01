from django.db import migrations


def backfill_assignee_logs(apps, schema_editor):
    """Ensure every assigned member has at least one TaskLog.

    Mirrors the runtime seeding in ``TaskAssignView``: any member assigned to
    a task without a corresponding activity (TaskLog) entry gets one, so the
    Activity & comments timeline shows a thread for every assignee. Covers
    tasks whose members were assigned before activity seeding existed (or via
    paths that didn't seed), e.g. imported/seeded data.
    """
    Task = apps.get_model("tasks", "Task")
    TaskLog = apps.get_model("tasks", "TaskLog")

    new_logs = []
    for task in Task.objects.prefetch_related("assigned_members", "task_logs"):
        logged_ids = {log.assigned_to_id for log in task.task_logs.all()}
        for member in task.assigned_members.all():
            if member.pk in logged_ids:
                continue
            new_logs.append(
                TaskLog(
                    task=task,
                    assigned_to=member,
                    # CompletedAs: "TEAM" when the membership is team-shaped
                    # (has a name), else "INDIVIDUAL" — matching the view.
                    completed_as="TEAM" if member.name else "INDIVIDUAL",
                )
            )
            logged_ids.add(member.pk)
    if new_logs:
        TaskLog.objects.bulk_create(new_logs)


def noop_reverse(apps, schema_editor):
    # Backfilled logs are indistinguishable from organically-created ones,
    # so there is nothing safe to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0005_task_assign_by_role_task_reviewers_task_watchers"),
    ]

    operations = [
        migrations.RunPython(backfill_assignee_logs, noop_reverse),
    ]
