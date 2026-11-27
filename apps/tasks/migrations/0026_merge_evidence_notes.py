"""One note per submission instead of one per evidence item.

Forwards, each TaskLog's item notes become its single ``evidence_note``:
one note is kept as it is; several are joined, a blank line apart, each
prefixed with the number of the link it was on ("Link 2: ...") so nothing
written is lost or loses its context. Items that held only a note (no link)
are then removed, and the remaining links renumbered from 0.

Backwards, the note goes onto the first link (or onto a new note-only item
when there are no links), as the per-item notes held it.
"""

from django.db import migrations

NOTE_LIMIT = 1000


def forwards(apps, schema_editor):
    TaskLog = apps.get_model("tasks", "TaskLog")
    TaskEvidence = apps.get_model("tasks", "TaskEvidence")
    by_log = {}
    for item in TaskEvidence.objects.order_by("task_log_id", "position", "id"):
        by_log.setdefault(item.task_log_id, []).append(item)
    for log_id, items in by_log.items():
        links = [item for item in items if item.url]
        noted = [item for item in items if item.note]
        if len(noted) == 1:
            note = noted[0].note
        else:
            parts = []
            for item in noted:
                if item.url:
                    parts.append(f"Link {links.index(item) + 1}: {item.note}")
                else:
                    parts.append(item.note)
            note = "\n\n".join(parts)
        TaskLog.objects.filter(pk=log_id).update(evidence_note=note[:NOTE_LIMIT])
        TaskEvidence.objects.filter(pk__in=[i.pk for i in items if not i.url]).delete()
        for position, item in enumerate(links):
            if item.position != position:
                TaskEvidence.objects.filter(pk=item.pk).update(position=position)


def backwards(apps, schema_editor):
    TaskLog = apps.get_model("tasks", "TaskLog")
    TaskEvidence = apps.get_model("tasks", "TaskEvidence")
    for log_id, note in TaskLog.objects.exclude(evidence_note="").values_list(
        "pk", "evidence_note"
    ):
        first = (
            TaskEvidence.objects.filter(task_log_id=log_id)
            .order_by("position", "id")
            .first()
        )
        if first is None:
            TaskEvidence.objects.create(task_log_id=log_id, url="", note=note, position=0)
        else:
            TaskEvidence.objects.filter(pk=first.pk).update(note=note)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0025_tasklog_evidence_note"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
