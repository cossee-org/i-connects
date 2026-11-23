"""Template helpers for comments."""

from __future__ import annotations

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.comments.services import mention_pattern

register = template.Library()


@register.filter
def with_mentions(comment):
    """The comment's text, escaped, with each tag in it highlighted.

    A tag is ``@Name`` or ``@email`` of someone actually tagged
    (``comment.mentions``): "@everyone" typed by hand, or a name whose tag
    was dropped, stays plain text. Matching runs on the escaped text against escaped names, so
    nothing the author typed is ever emitted unescaped. Newlines are left as
    they are for ``whitespace-pre-line`` to render. ``.comment-mention``
    (base.html) is the same chip the comment box paints while typing.
    """
    text = escape(comment.content or "")
    users = [m.user for m in comment.mentions.all()]
    pattern = mention_pattern(
        [escape(u.display_name) for u in users], [escape(u.email) for u in users]
    )
    if pattern is None:
        return mark_safe(text)
    return mark_safe(
        pattern.sub(
            lambda m: f'<span class="comment-mention">{m.group(0)}</span>',
            text,
        )
    )
