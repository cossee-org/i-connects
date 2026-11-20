"""Template filters for task assignee/reviewer names and date labels."""

from __future__ import annotations

from django import template

from apps.tasks.calendar import task_date_label

register = template.Library()


@register.filter
def member_name_summary(members, short_limit=2):
    """Return {'full': all names comma-separated, 'short': first short_limit
    names + an "and N others" overflow suffix}, for a list/queryset of
    ``ProjectMember`` objects.
    """
    members = list(members or [])
    if not members:
        return {"full": "", "short": ""}

    names = [m.display_name for m in members]
    full = ", ".join(names)

    visible = names[:short_limit]
    overflow = len(names) - len(visible)
    short = ", ".join(visible)
    if overflow:
        short += f" and {overflow} other{'' if overflow == 1 else 's'}"

    return {"full": full, "short": short}


@register.filter
def task_dates(task):
    """``{'short': ..., 'full': ...}`` for a task's date span — see
    ``apps.tasks.calendar.task_date_label``. Rendered by
    ``_components/task_dates.html``."""
    return task_date_label(task)


@register.filter
def due_hint(task, member=None):
    """How long an open task has left, beside its dates: "4 days left",
    "1 day left" or "Due today". Empty for a task with no deadline, one
    already completed, and one past due — the Overdue (or Extended) pill
    says that.

    Pass the viewer's ``ProjectMember`` (``task|due_hint:project_member``):
    when a Lead gave them an individual deadline, the hint speaks to theirs
    instead — "Your deadline Mar 19 · 4 days left", or "Your deadline
    passed Mar 12" — since the dates beside it are the task's.
    """
    from django.utils import timezone

    from apps.core.enums import TaskStatus

    if task.status == TaskStatus.COMPLETED:
        return ""
    personal = None
    if member and getattr(member, "pk", None):
        personal = task.personal_deadlines().get(member.pk)
    if personal is not None:
        date = timezone.localtime(personal).strftime("%b %-d")
        if personal < timezone.now():
            return f"Your deadline passed {date}"
        return f"Your deadline {date} · {_days_left(personal)}"
    if not task.deadline or task.is_past_due:
        return ""
    return _days_left(task.deadline)


def _days_left(deadline) -> str:
    from django.utils import timezone

    days = (timezone.localdate(deadline) - timezone.localdate()).days
    if days <= 0:
        return "Due today"
    return f"{days} day{'' if days == 1 else 's'} left"
