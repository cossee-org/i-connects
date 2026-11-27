"""Move each TaskLog's single evidence link and note into one TaskEvidence
item at position 0. Reversible: going back copies the first item onto the
TaskLog again (any further items are lost, as the old fields held one)."""

from django.db import migrations


def forwards(apps, schema_editor):
    TaskLog = apps.get_model("tasks", "TaskLog")
    TaskEvidence = apps.get_model("tasks", "TaskEvidence")
    items = [
        TaskEvidence(task_log_id=pk, url=url, note=note, position=0)
        for pk, url, note in TaskLog.objects.exclude(
            evidence_url="", evidence_note=""
        ).values_list("pk", "evidence_url", "evidence_note")
        if url or note
    ]
    TaskEvidence.objects.bulk_create(items, batch_size=500)


def backwards(apps, schema_editor):
    TaskLog = apps.get_model("tasks", "TaskLog")
    TaskEvidence = apps.get_model("tasks", "TaskEvidence")
    seen = set()
    for item in TaskEvidence.objects.order_by("task_log_id", "position", "id"):
        if item.task_log_id in seen:
            continue
        seen.add(item.task_log_id)
        TaskLog.objects.filter(pk=item.task_log_id).update(
            evidence_url=item.url, evidence_note=item.note
        )


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0022_taskevidence"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
