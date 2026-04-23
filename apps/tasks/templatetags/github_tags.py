"""Template filters for rendering GitHub issue / PR links on tasks."""

from __future__ import annotations

from django import template

from apps.tasks.github_links import parse_github_url

register = template.Library()


@register.filter
def github_link(url):
    return parse_github_url(url)
