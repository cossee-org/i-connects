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
