"""Template filters for the contributions app."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    """Return ``d[key]`` (or 0 if missing / d is None).

    Django's template language can't index a dict by a variable key with
    ordinary dot notation, so this filter is used to read per-type counts
    out of the matrix row dicts in the project contributions template.
    """
    if d is None:
        return 0
    return d.get(key, 0)


@register.filter
def display_name(user) -> str:
    """A user's full name, falling back to their account name, then email.

    The same rule the rest of the app reads a person by, as a filter so the
    contribution tables can name someone without the view assembling it.
    """
    from apps.contributions.services import user_display_name

    if user is None:
        return ""
    return user_display_name(user)
