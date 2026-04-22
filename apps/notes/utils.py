"""Utility helpers for the notes app."""

from __future__ import annotations

import html

import markdown as markdown_lib
from django.utils.safestring import mark_safe


def render_markdown(text: str | None) -> str:
    """Render Markdown to sanitised HTML.

    Raw HTML in the source is escaped *before* being handed to the
    Markdown renderer, so user content cannot inject arbitrary tags.
    The returned value is marked safe because after escaping the input
    the rendered output contains only the HTML that Markdown itself
    produced.
    """
    if not text:
        return ""
    escaped = html.escape(text)
    rendered = markdown_lib.markdown(
        escaped,
        extensions=["extra", "sane_lists", "nl2br"],
        output_format="html5",
    )
    return mark_safe(rendered)
