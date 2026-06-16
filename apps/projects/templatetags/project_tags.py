"""Template helpers for the projects app."""

from __future__ import annotations

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
