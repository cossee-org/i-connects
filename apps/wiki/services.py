"""Write path for the wiki.

Every mutation goes through this module rather than through a form's
``save()`` so that the three things a wiki edit must do together — update the
page, append its revision, and audit-log it — cannot come apart. Views call
these; nothing else writes ``WikiPage`` or ``WikiRevision``.
"""

from __future__ import annotations

from typing import Callable

from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.text import slugify

from apps.audit.models import AuditAction
from apps.audit.services import log_action

from .models import WikiPage, WikiRevision

#: Slugs the wiki's own URLs claim. A page titled "New" would otherwise
#: generate ``/wiki/new/``, which routes to the create form — the page would
#: exist but be unreachable.
RESERVED_SLUGS = frozenset({"new"})


class EditConflict(Exception):
    """Someone else saved the page while this editor had it open.

    Carries the revision the editor started from and the one that is now
    current, so the form can say what happened rather than silently
    discarding one of the two edits.
    """

    def __init__(self, *, expected: int, current: int):
        super().__init__(
            f"Page has moved on: editing revision {expected}, current is {current}."
        )
        self.expected = expected
        self.current = current


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------
def unique_slug(project, title: str, *, exclude_pk: int | None = None) -> str:
    """A slug for ``title`` that no other page in ``project`` is using.

    Falls back to ``page`` for a title that slugifies to nothing (all
    punctuation, or a script ``slugify`` strips), then appends ``-2``,
    ``-3``… until it is free.
    """
    base = slugify(title)[:200] or "page"
    taken = set(
        WikiPage.objects.filter(project=project)
        .exclude(pk=exclude_pk)
        .values_list("slug", flat=True)
    )
    candidate = base
    suffix = 2
    while candidate in taken or candidate in RESERVED_SLUGS:
        candidate = f"{base[:210]}-{suffix}"
        suffix += 1
    return candidate


# ---------------------------------------------------------------------------
# Wiki links
# ---------------------------------------------------------------------------
def link_resolver(project) -> Callable[[str], tuple[str, bool]]:
    """A ``[[link]]`` resolver for ``project``, backed by one query.

    Resolution order is title first, then slug: a page keeps its address
    when it is renamed (see ``WikiPage``), so matching on title is what
    makes ``[[The new name]]`` find it. Matching is case-insensitive because
    prose refers to a page mid-sentence as often as by its exact heading.

    An unresolved target points at the create form with the title prefilled,
    and is reported as not existing so the template can mark it.
    """
    pages = list(
        WikiPage.objects.filter(project=project).values_list("title", "slug")
    )
    by_title = {title.casefold(): slug for title, slug in pages}
    by_slug = {slug.casefold(): slug for _, slug in pages}

    def resolve(target: str) -> tuple[str, bool]:
        key = target.strip().casefold()
        slug = by_title.get(key) or by_slug.get(key) or by_slug.get(slugify(target))
        if slug:
            return (
                reverse(
                    "wiki:detail",
                    kwargs={"project_pk": project.pk, "slug": slug},
                ),
                True,
            )
        create_url = reverse("wiki:create", kwargs={"project_pk": project.pk})
        return f"{create_url}?{urlencode({'title': target.strip()})}", False

    return resolve


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------
def _append_revision(page: WikiPage, *, actor, summary: str) -> WikiRevision:
    """Snapshot ``page`` as it now stands. Caller holds the transaction."""
    next_number = (
        WikiRevision.objects.filter(page=page).aggregate(top=Max("number"))["top"] or 0
    ) + 1
    return WikiRevision.objects.create(
        page=page,
        number=next_number,
        title=page.title,
        content_markdown=page.content_markdown,
        summary=summary[:255],
        editor=actor if getattr(actor, "pk", None) else None,
    )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@transaction.atomic
def create_page(
    *,
    project,
    actor,
    title: str,
    content_markdown: str = "",
    summary: str = "",
    locked: bool = False,
    show_on_overview: bool = False,
) -> WikiPage:
    """Create a page, its first revision, and the audit entry, atomically."""
    page = WikiPage.objects.create(
        project=project,
        title=title.strip(),
        slug=unique_slug(project, title),
        content_markdown=content_markdown or "",
        locked=locked,
        show_on_overview=show_on_overview,
        created_by=actor if getattr(actor, "pk", None) else None,
        updated_by=actor if getattr(actor, "pk", None) else None,
    )
    _append_revision(page, actor=actor, summary=summary or "Page created")
    log_action(
        AuditAction.WIKI_PAGE_CREATED,
        actor=actor,
        entity=page,
        project=project,
        metadata={"title": page.title, "slug": page.slug},
    )
    return page


@transaction.atomic
def update_page(
    *,
    page: WikiPage,
    actor,
    title: str,
    content_markdown: str,
    summary: str = "",
    locked: bool | None = None,
    show_on_overview: bool | None = None,
    expected_revision: int | None = None,
) -> tuple[WikiPage, bool]:
    """Save an edit. Returns ``(page, a_revision_was_written)``.

    A save that changes neither the title nor the body writes no revision —
    an "Update" button pressed twice should not fill the history with
    identical snapshots. Changing only a setting (the lock, being on the
    Project Overview) still counts as a change to the page (and is
    audit-logged), just not to its text. ``None`` leaves a setting as is.

    ``expected_revision`` is the revision the editor loaded. If the page has
    moved on since, this raises :class:`EditConflict` instead of overwriting
    the other edit.
    """
    # Lock the row for the whole read-modify-write: two simultaneous saves
    # would otherwise both read the same latest revision number and race on
    # the (page, number) uniqueness constraint.
    page = WikiPage.objects.select_for_update().get(pk=page.pk)
    current_revision = page.latest_revision_number
    if expected_revision is not None and expected_revision != current_revision:
        raise EditConflict(expected=expected_revision, current=current_revision)

    new_title = title.strip()
    new_body = content_markdown or ""
    text_changed = new_title != page.title or new_body != page.content_markdown
    settings_changed = (locked is not None and locked != page.locked) or (
        show_on_overview is not None and show_on_overview != page.show_on_overview
    )
    if not text_changed and not settings_changed:
        return page, False

    page.title = new_title
    page.content_markdown = new_body
    if locked is not None:
        page.locked = locked
    if show_on_overview is not None:
        page.show_on_overview = show_on_overview
    if getattr(actor, "pk", None):
        page.updated_by = actor
    page.save()

    if text_changed:
        _append_revision(page, actor=actor, summary=summary)
    log_action(
        AuditAction.WIKI_PAGE_UPDATED,
        actor=actor,
        entity=page,
        project=page.project,
        metadata={
            "title": page.title,
            "slug": page.slug,
            "text_changed": text_changed,
            "locked": page.locked,
            "show_on_overview": page.show_on_overview,
        },
    )
    return page, text_changed


@transaction.atomic
def restore_revision(*, page: WikiPage, revision: WikiRevision, actor) -> WikiPage:
    """Bring ``revision``'s text back as a new revision on top of history.

    Nothing is rewound: the history keeps every step, including the ones
    being undone, and gains one more saying where the text came from.
    """
    if revision.page_id != page.pk:
        raise ValueError("Revision belongs to a different page.")

    page = WikiPage.objects.select_for_update().get(pk=page.pk)
    page.title = revision.title
    page.content_markdown = revision.content_markdown
    if getattr(actor, "pk", None):
        page.updated_by = actor
    page.save()
    _append_revision(
        page,
        actor=actor,
        summary=f"Restored revision {revision.number}",
    )
    log_action(
        AuditAction.WIKI_PAGE_RESTORED,
        actor=actor,
        entity=page,
        project=page.project,
        metadata={
            "title": page.title,
            "slug": page.slug,
            "restored_revision": revision.number,
        },
    )
    return page


def delete_page(*, page: WikiPage, actor) -> None:
    """Delete a page and its history, leaving the audit entry behind."""
    title, slug, project = page.title, page.slug, page.project
    revision_count = page.revisions.count()
    page.delete()
    log_action(
        AuditAction.WIKI_PAGE_DELETED,
        actor=actor,
        entity_type="wikipage",
        project=project,
        metadata={"title": title, "slug": slug, "revisions": revision_count},
    )
