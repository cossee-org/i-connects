"""Template helpers for the projects app."""

from __future__ import annotations

from datetime import datetime, timezone

from django import template

from apps.core.enums import MemberPrivileges

register = template.Library()


@register.filter
def privilege_label(value) -> str:
    """Return the human-readable label for a MemberPrivileges value."""
    if not value:
        return ""
    try:
        return MemberPrivileges(value).label
    except ValueError:
        return str(value)


@register.filter
def truncate_members(members, char_budget=42):
    """Render a list of ProjectPrivilege objects as 'Name, Name and N more'."""
    if not members:
        return ""
    visible, used = [], 0
    for priv in members:
        u = priv.user
        name = (
            f"{u.given_name} {u.family_name}".strip()
            or u.name
            or u.email
        )
        # Always include at least one name
        if visible and used + len(name) + 2 > char_budget:
            break
        visible.append(name)
        used += len(name) + 2
    overflow = len(members) - len(visible)
    result = ", ".join(visible)
    if overflow:
        result += f" and {overflow} more"
    return result


@register.filter
def relative_updated(dt) -> str:
    """Return a human-friendly 'Updated …' string for a datetime."""
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    # Make naive datetimes timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 3600:
        return "Updated just now"
    hours = total_seconds // 3600
    if hours < 24:
        return f"Updated {hours} hour{'s' if hours != 1 else ''} ago"
    days = total_seconds // 86400
    if days < 7:
        return f"Updated {days} day{'s' if days != 1 else ''} ago"
    weeks = days // 7
    if weeks <= 4:
        return f"Updated {weeks} week{'s' if weeks != 1 else ''} ago"
    return f"Updated on {dt.strftime('%b %-d, %Y')}"
