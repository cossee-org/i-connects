"""Template filters and tags for the contributions app."""

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


@register.inclusion_tag("projects/partials/matrix_sort_header.html")
def matrix_sort_header(matrix, key, label, project_pk, only_used, align="right"):
    """One sortable column header of the contributions matrix.

    Every header is the same button with a different column behind it, so
    it lives here rather than three times over in the table: the member
    name, each CRediT role, and the total.

    Clicking the column you are already sorted by flips the direction;
    clicking any other starts it at its natural end — names read forwards,
    counts read biggest-first.
    """
    from apps.contributions.services import MATRIX_SORT_MEMBER

    active = matrix.get("sort") == key
    direction = matrix.get("direction")
    if active:
        next_direction = "asc" if direction == "desc" else "desc"
        aria_sort = "descending" if direction == "desc" else "ascending"
    else:
        next_direction = "asc" if key == MATRIX_SORT_MEMBER else "desc"
        aria_sort = "none"
    return {
        "key": key,
        "label": label,
        "project_pk": project_pk,
        "only_used": only_used,
        "align": align,
        "active": active,
        "direction": direction,
        "next_direction": next_direction,
        "aria_sort": aria_sort,
    }
