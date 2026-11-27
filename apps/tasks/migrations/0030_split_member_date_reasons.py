"""Give a member's own start date and own deadline a reason each.

``AssigneeDeadline.reason`` covered both of a member's own dates. Now that a
Lead sets each date in a dialog of its own, it is split into ``start_reason``
and ``deadline_reason``. Each existing row keeps its reason on the dates it
has.
"""

from django.db import migrations, models


def split_reason(apps, schema_editor):
    AssigneeDeadline = apps.get_model("tasks", "AssigneeDeadline")
    for entry in AssigneeDeadline.objects.exclude(reason=""):
        entry.start_reason = entry.reason if entry.start_date else ""
        entry.deadline_reason = entry.reason if entry.deadline else ""
        entry.save(update_fields=["start_reason", "deadline_reason"])


def join_reason(apps, schema_editor):
    AssigneeDeadline = apps.get_model("tasks", "AssigneeDeadline")
    for entry in AssigneeDeadline.objects.all():
        entry.reason = entry.deadline_reason or entry.start_reason
        entry.save(update_fields=["reason"])


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0029_split_member_date_timezones"),
    ]

    operations = [
        migrations.AddField(
            model_name="assigneedeadline",
            name="start_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="assigneedeadline",
            name="deadline_reason",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.RunPython(split_reason, join_reason),
        migrations.RemoveField(model_name="assigneedeadline", name="reason"),
    ]
