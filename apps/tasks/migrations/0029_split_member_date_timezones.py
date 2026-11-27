"""Give a member's own start date and own deadline a timezone each.

``AssigneeDeadline.timezone`` governed both of a member's own dates. It is
split into ``start_timezone`` and ``deadline_timezone``, so a Lead can set a
member's start in one zone and their deadline in another. Each existing row
keeps its zone on the dates it has: a zone is only kept beside a date that
is set, so a row with only a start keeps an empty ``deadline_timezone``.
"""

from django.db import migrations, models

import apps.tasks.models
import apps.tasks.timezones


def split_zone(apps, schema_editor):
    AssigneeDeadline = apps.get_model("tasks", "AssigneeDeadline")
    for entry in AssigneeDeadline.objects.exclude(timezone=""):
        entry.start_timezone = entry.timezone if entry.start_date else ""
        entry.deadline_timezone = entry.timezone if entry.deadline else ""
        entry.save(update_fields=["start_timezone", "deadline_timezone"])


def join_zone(apps, schema_editor):
    AssigneeDeadline = apps.get_model("tasks", "AssigneeDeadline")
    for entry in AssigneeDeadline.objects.all():
        # One zone again: the deadline's, else the start's.
        entry.timezone = entry.deadline_timezone or entry.start_timezone
        entry.save(update_fields=["timezone"])


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0028_rename_evidence_to_attachments"),
    ]

    operations = [
        migrations.AddField(
            model_name="assigneedeadline",
            name="start_timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="IANA timezone governing this member's own start date. Empty follows the task's zone.",
                max_length=64,
                validators=[apps.tasks.timezones.validate_timezone],
            ),
        ),
        migrations.AddField(
            model_name="assigneedeadline",
            name="deadline_timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text="IANA timezone governing this member's own deadline. Empty follows the task's zone.",
                max_length=64,
                validators=[apps.tasks.timezones.validate_timezone],
            ),
        ),
        migrations.RunPython(split_zone, join_zone),
        migrations.RemoveField(
            model_name="assigneedeadline",
            name="timezone",
        ),
        migrations.AlterField(
            model_name="task",
            name="timezone",
            field=models.CharField(
                default=apps.tasks.models.default_task_timezone,
                help_text="IANA timezone governing the task's start and due dates: the Lead's clock time is read in it, and the dates are shown in it. An assignee's own dates may carry a zone of their own (``AssigneeDeadline.start_timezone`` / ``deadline_timezone``).",
                max_length=64,
                validators=[apps.tasks.timezones.validate_timezone],
            ),
        ),
    ]
