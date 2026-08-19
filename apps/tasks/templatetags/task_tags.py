"""Template filters for rendering task assignee/reviewer name summaries."""

from __future__ import annotations

from django import template

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
