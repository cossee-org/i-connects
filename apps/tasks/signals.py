"""Signal handlers for the tasks app.

An individual deadline (``AssigneeDeadline``) belongs to the assignment, so
it goes when the member comes off the task — however that happens: the
assign control, the task form, a CSV import or the reverse side of the
relation.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from apps.tasks.models import AssigneeDeadline, Task


@receiver(m2m_changed, sender=Task.assigned_members.through)
def drop_deadlines_of_unassigned(sender, instance, action, reverse, pk_set, **kwargs):
    if action == "post_remove" and pk_set:
        if reverse:  # instance is a ProjectMember; pk_set holds task ids
            AssigneeDeadline.objects.filter(member=instance, task_id__in=pk_set).delete()
        else:
            AssigneeDeadline.objects.filter(task=instance, member_id__in=pk_set).delete()
    elif action == "post_clear":
        if reverse:
            AssigneeDeadline.objects.filter(member=instance).delete()
        else:
            AssigneeDeadline.objects.filter(task=instance).delete()
