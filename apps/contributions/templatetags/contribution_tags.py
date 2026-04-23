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
