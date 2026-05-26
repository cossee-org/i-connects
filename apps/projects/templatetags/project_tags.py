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
