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
    when a Lead gave them their own dates, the hint speaks to those instead,
    since the dates beside it are the task's — "Your start Nov 20", then
    "Your deadline Nov 25 · 4 days left" or "Your deadline passed Nov 12",
    joined with a middle dot.
    """
    from django.utils import timezone

    from apps.core.enums import TaskStatus

    if task.status == TaskStatus.COMPLETED:
        return ""
    own_start = own_deadline = None
    if member and getattr(member, "pk", None):
        own_start = task.personal_start_dates().get(member.pk)
        own_deadline = task.personal_deadlines().get(member.pk)
    parts = []
    if own_start is not None:
        parts.append(f"Your start {timezone.localtime(own_start):%b %-d}")
    if own_deadline is not None:
        date = f"{timezone.localtime(own_deadline):%b %-d}"
        if own_deadline < timezone.now():
            parts.append(f"Your deadline passed {date}")
        else:
            parts.append(f"Your deadline {date} · {_days_left(own_deadline)}")
    elif task.deadline and not task.is_past_due:
        parts.append(_days_left(task.deadline))
    return " · ".join(parts)


def _days_left(deadline) -> str:
    from django.utils import timezone

    days = (timezone.localdate(deadline) - timezone.localdate()).days
    if days <= 0:
        return "Due today"
    return f"{days} day{'' if days == 1 else 's'} left"
