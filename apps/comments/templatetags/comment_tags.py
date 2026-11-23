"""Template helpers for comments."""

from __future__ import annotations

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.comments.services import mention_pattern

register = template.Library()


@register.filter
def with_mentions(comment):
    """The comment's text, escaped, with each ``@Name`` it tags highlighted.

    Only the names of people actually tagged (``comment.mentions``) are
    marked: "@everyone" typed by hand, or a name whose tag was dropped, stays
    plain text. Matching runs on the escaped text against escaped names, so
    nothing the author typed is ever emitted unescaped. Newlines are left as
    they are for ``whitespace-pre-line`` to render.
    """
    text = escape(comment.content or "")
    names = [escape(m.user.display_name) for m in comment.mentions.all()]
    pattern = mention_pattern(names)
    if pattern is None:
        return mark_safe(text)
    return mark_safe(
        pattern.sub(
            lambda m: f'<span class="comment-mention font-medium text-brand-navy">{m.group(0)}</span>',
            text,
        )
    )
