"""Rendering helpers for the wiki: markdown, ``[[wiki links]]`` and diffs."""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass, field
from typing import Callable

import markdown as markdown_lib
from django.utils.safestring import mark_safe
from markdown.treeprocessors import Treeprocessor

#: Schemes a link in page text may carry. Anything else — ``javascript:``,
#: ``data:``, ``vbscript:`` — is a script vector rather than a destination.
#: A URL with no scheme at all (``/projects/1/``, ``#method``, ``page.pdf``)
#: is relative and always allowed.
SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "ftp", "ftps", "tel"})

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.IGNORECASE)
#: Characters a hostile URL hides inside a scheme to slip past a check —
#: ``java\tscript:`` and ``java\x00script:`` are both live in some browsers.
_URL_NOISE_RE = re.compile(r"[\s\x00-\x20\x7f]+")


def is_safe_url(url: str | None) -> bool:
    """Whether ``url`` is safe to put in an ``href``/``src``.

    Markdown itself does not police link targets, so ``[go](javascript:…)``
    would otherwise render as a working script link even though no raw HTML
    survived the escaping.
    """
    if not url:
        return True
    match = _SCHEME_RE.match(_URL_NOISE_RE.sub("", url))
    if match is None:
        return True
    return match.group(1).lower() in SAFE_URL_SCHEMES


class _NeutraliseUnsafeUrls(Treeprocessor):
    """Blank out link and image targets that carry an unsafe scheme."""

    def run(self, root):
        for tag, attribute in (("a", "href"), ("img", "src")):
            for element in root.iter(tag):
                if not is_safe_url(element.get(attribute)):
                    element.set(attribute, "")


class _WikiSafetyExtension(markdown_lib.extensions.Extension):
    def extendMarkdown(self, md):
        # After ``inline`` (which is where links are built) and late enough
        # that no other extension re-introduces a target afterwards.
        md.treeprocessors.register(
            _NeutraliseUnsafeUrls(md), "wiki_safe_urls", 1
        )

#: ``[[Page title]]`` or ``[[Page title|the words to show]]``. Kept
#: deliberately narrow: no brackets or pipes inside either half, so an
#: unclosed or nested bracket is left alone as literal text rather than
#: swallowing the rest of the paragraph.
WIKILINK_RE = re.compile(r"\[\[([^\[\]|\n]+?)(?:\|([^\[\]|\n]+?))?\]\]")


@dataclass
class RenderedWikiText:
    """Rendered page body plus what the render learned along the way."""

    html: str
    toc: str = ""
    #: Titles a ``[[link]]`` pointed at that no page answers to yet. The
    #: page footer offers to create them.
    missing_links: list[str] = field(default_factory=list)


def _wikilink_replacement(
    target: str, label: str, resolve: Callable[[str], tuple[str, bool] | None]
) -> str:
    """One ``<a>`` for a wiki link, as raw HTML markdown will pass through.

    ``target`` and ``label`` arrive already HTML-escaped (the whole source
    is escaped before substitution runs), so they are safe to interpolate;
    ``resolve`` is handed the *unescaped* title because it matches against
    page titles in the database.
    """
    resolved = resolve(html.unescape(target))
    if resolved is None:
        # No resolver (e.g. rendering a bare revision with no project
        # context) — show the link text without inventing a destination.
        return label
    url, exists = resolved
    if exists:
        return f'<a class="wiki-link" href="{html.escape(url, quote=True)}">{label}</a>'
    return (
        f'<a class="wiki-link wiki-link-missing" '
        f'href="{html.escape(url, quote=True)}" '
        f'title="This page does not exist yet — create it">{label}</a>'
    )


def render_wiki_markdown(
    text: str | None,
    *,
    resolve_link: Callable[[str], tuple[str, bool] | None] | None = None,
    with_toc: bool = False,
) -> RenderedWikiText:
    """Render wiki markdown to sanitised HTML.

    Two things make the result safe to mark safe. ``<`` is neutralised in
    the source before the renderer sees it, so page text cannot open a tag:
    the only HTML in the output is what markdown itself produced, plus the
    anchors this function builds for ``[[wiki links]]``. And every link and
    image target markdown *did* produce is checked against
    ``SAFE_URL_SCHEMES``, because ``[go](javascript:…)`` needs no raw HTML
    at all.

    ``resolve_link`` maps a link target to ``(url, page_exists)``. A link to
    a page that does not exist yet still renders — pointing at the "new
    page" form, prefilled — which is how a wiki grows an outline before it
    grows its pages.
    """
    if not text:
        return RenderedWikiText(html="", toc="")

    # Only ``<`` is escaped, not the whole of ``html.escape``. Escaping
    # ``>`` too would take blockquotes ("> a note") with it, and escaping
    # ``&`` is python-markdown's job — it emits ``&amp;`` for a bare
    # ampersand itself. Neutralising ``<`` alone is what stops raw HTML:
    # with no ``<`` there is no tag, no comment and no CDATA section,
    # whatever else the source contains.
    escaped = text.replace("<", "&lt;")
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        label = (match.group(2) or match.group(1)).strip()
        if not target:
            return match.group(0)

        def _resolve(title: str) -> tuple[str, bool] | None:
            if resolve_link is None:
                return None
            resolved = resolve_link(title)
            if resolved is not None and not resolved[1] and title not in missing:
                missing.append(title)
            return resolved

        return _wikilink_replacement(target, label, _resolve)

    linked = WIKILINK_RE.sub(_sub, escaped)

    extensions: list[object] = [
        "extra",
        "sane_lists",
        "nl2br",
        _WikiSafetyExtension(),
    ]
    extension_configs: dict[str, dict[str, object]] = {}
    if with_toc:
        extensions.append("toc")
        # Anchors on every heading (so the contents list can link to them)
        # but no visible ¶ marker, and only h2/h3 in the list — an h1 inside
        # the body duplicates the page title.
        extension_configs["toc"] = {"permalink": False, "toc_depth": "2-3"}

    md = markdown_lib.Markdown(
        extensions=extensions,
        extension_configs=extension_configs,
        output_format="html5",
    )
    rendered = md.convert(linked)
    # ``md.toc`` is a fragment python-markdown built from the heading ids it
    # generated; the heading text inside it went through the same escaping.
    # With no headings in range it is still a non-empty ``<div><ul></ul></div>``,
    # so ``toc_tokens`` — not the markup — decides whether there is a contents
    # list worth showing.
    toc = ""
    if with_toc and getattr(md, "toc_tokens", None):
        toc = md.toc
    return RenderedWikiText(
        html=mark_safe(rendered),
        toc=mark_safe(toc),
        missing_links=missing,
    )


def _end_sentence(text: str) -> str:
    """``text`` with a full stop, unless it already ends in punctuation."""
    text = text.strip()
    if not text:
        return ""
    return text if text[-1] in ".,;:!?…" else text + "."


def excerpt(text: str | None, limit: int = 180) -> str:
    """A one-line plain-text preview of a page, for the wiki index.

    Markdown syntax is stripped rather than rendered so the index reads as
    prose instead of as ``## Heading`` and ``[[link]]`` noise.
    """
    if not text:
        return ""
    plain = WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    # Fenced code blocks go first, then the block-level markers. A heading or
    # a list item ends with a full stop it may not have written, because the
    # excerpt runs the whole page onto one line and "Before you leave Charge
    # the tablet" reads as a sentence that lost a word.
    plain = re.sub(r"```.*?```", " ", plain, flags=re.DOTALL)
    plain = re.sub(
        r"^\s{0,3}#{1,6}\s*(.*)$",
        lambda m: _end_sentence(m.group(1)),
        plain,
        flags=re.MULTILINE,
    )
    plain = re.sub(r"^\s{0,3}>\s?", "", plain, flags=re.MULTILINE)
    plain = re.sub(
        r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+(.*)$",
        lambda m: _end_sentence(m.group(1)),
        plain,
        flags=re.MULTILINE,
    )
    plain = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", plain)
    plain = re.sub(r"[*_`~]", "", plain)
    plain = " ".join(plain.split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + "…"


@dataclass
class DiffLine:
    """One row of a rendered diff. ``kind`` drives the row's styling."""

    kind: str  # "added" | "removed" | "context" | "hunk"
    text: str


def diff_lines(old: str | None, new: str | None, *, context: int = 3) -> list[DiffLine]:
    """A unified diff of two page bodies, as rows a template can style.

    Empty when the two texts are identical, which is what the history page
    keys "no text changes in this revision" off.
    """
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    rows: list[DiffLine] = []
    for line in difflib.unified_diff(old_lines, new_lines, n=context, lineterm=""):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            rows.append(DiffLine("hunk", line))
        elif line.startswith("+"):
            rows.append(DiffLine("added", line[1:]))
        elif line.startswith("-"):
            rows.append(DiffLine("removed", line[1:]))
        else:
            rows.append(DiffLine("context", line[1:] if line.startswith(" ") else line))
    return rows


def summarise_change(old: str | None, new: str | None) -> str:
    """"+12 −3 lines" for a revision, or "" when the body is unchanged."""
    added = removed = 0
    for row in diff_lines(old, new, context=0):
        if row.kind == "added":
            added += 1
        elif row.kind == "removed":
            removed += 1
    if not added and not removed:
        return ""
    parts: list[str] = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"−{removed}")
    return " ".join(parts) + (" line" if added + removed == 1 else " lines")
