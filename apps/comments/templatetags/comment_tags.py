"""Template helpers for comments."""

from __future__ import annotations

import re

from django import template
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.comments.services import mention_pattern

register = template.Library()

# A web address written into a comment: http(s):// or www., up to the next
# space or a character that cannot be in one. Other schemes (javascript:,
# data:, mailto:) are never linked. Not urlize: it would also turn the address
# in an @email tag into a mailto link, and a bare "notes.md" into a website.
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)

# What is left must still be an address: the prefix, then something more.
_ADDRESS = re.compile(r"(?:https?://|www\.)[^\s.]", re.IGNORECASE)

# Punctuation that ends a sentence rather than the address it follows.
_TRAILING = ".,;:!?*_~"
_CLOSERS = {")": "(", "]": "["}

LINK_CLASS = "comment-link text-brand-navy underline underline-offset-2 hover:text-brand-coral"


def _trim(url: str) -> str:
    """``url`` without the punctuation that follows it in the sentence.

    A closing bracket stays when the address opened it — the Wikipedia
    "Foo_(bar)" kind — and goes when it closes a bracket around the address.
    """
    while url:
        last = url[-1]
        if last in _TRAILING:
            url = url[:-1]
        elif last in _CLOSERS and url.count(last) > url.count(_CLOSERS[last]):
            url = url[:-1]
        else:
            break
    return url


def _link(url: str) -> str:
    href = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
    return (
        f'<a href="{escape(href)}" class="{LINK_CLASS}" '
        f'target="_blank" rel="noopener noreferrer nofollow" data-external-link>'
        f"{escape(url)}</a>"
    )


def _tagger(comment):
    """A function that escapes plain text and marks the tags in it."""
    users = [m.user for m in comment.mentions.all()]
    pattern = mention_pattern(
        [escape(u.display_name) for u in users], [escape(u.email) for u in users]
    )
    if pattern is None:
        return escape

    by_name: dict[str, list] = {}
    by_email = {}
    for u in users:
        by_name.setdefault(str(escape(u.display_name)), []).append(u)
        by_email[str(escape(u.email)).lower()] = u

    def chip(match):
        tag = match.group(0)
        said = tag[1:]
        named = by_name.get(said, [])
        user = by_email.get(said.lower()) or (named[0] if len(named) == 1 else None)
        if user is None:
            return f'<span class="comment-mention">{tag}</span>'
        href = reverse("public_profile", kwargs={"pk": user.pk})
        return f'<a href="{href}" class="comment-mention">{tag}</a>'

    return lambda plain: pattern.sub(chip, str(escape(plain)))


@register.filter
def comment_body(comment):
    """The comment's text, escaped, with its web addresses and tags linked.

    Web addresses (``URL``) open in a new tab, after the "Leaving i-CONNECTS"
    dialog (base.html) has asked — ``data-external-link``. They are found
    first, in the text as typed, so an ``@`` inside one
    (``https://x.com/@reefs``) is never read as a tag.

    A tag is ``@Name`` or ``@email`` of someone actually tagged
    (``comment.mentions``): "@everyone" typed by hand, or a name whose tag
    was dropped, stays plain text. Each tag links to the tagged person's
    profile. Two people tagged under one name (two Ben Okafors, both picked)
    leave that name unsure of whom it means, so it is highlighted but not
    linked; their addresses, being unique, always are. ``.comment-mention``
    (base.html) is the same chip the comment box paints while typing.

    Everything the author typed is escaped before it is emitted, including
    inside an ``href``. Newlines are left as they are for
    ``whitespace-pre-line`` to render.
    """
    text = comment.content or ""
    tag = _tagger(comment)
    out, last = [], 0
    for match in URL.finditer(text):
        url = _trim(match.group(0))
        if not _ADDRESS.match(url):  # "www." or "https://" alone, once trimmed
            continue
        out.append(tag(text[last : match.start()]))
        out.append(_link(url))
        last = match.start() + len(url)
    out.append(tag(text[last:]))
    return mark_safe("".join(out))
