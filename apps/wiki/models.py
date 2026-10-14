"""Wiki app: project-scoped documentation pages with a full revision history.

Two models:

- ``WikiPage`` — the current state of one page. There is one row per page,
  and it always holds the latest text: reading a page never has to walk the
  history.
- ``WikiRevision`` — an append-only snapshot per save. Every edit writes one,
  including the page's creation, so revision 1 is always "how the page
  started" and the newest revision always matches the page itself. Rows are
  immutable, which is what makes "restore revision 3" trustworthy: restoring
  writes a *new* revision carrying the old text rather than rewinding
  history.

The page's ``slug`` is derived from its title once, at creation, and then
stays put. A wiki's value is that links into it keep working, so renaming a
page moves neither its address nor anyone's bookmark. ``[[Wiki links]]``
resolve on title first (see ``apps.wiki.utils``), so a renamed page is still
reachable by its new name from inside the wiki.
"""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, models
from django.urls import reverse


class WikiVisibility(models.TextChoices):
    """Who may read a page. A lead sets it; ``WikiPage.objects.visible_to``
    applies it everywhere pages are read."""

    LEADS = "leads", "Leads only"
    MEMBERS = "members", "All contributors"
    # Anyone who can see the project. Only a public project shows anything
    # to non-members, so in a private one this reads as MEMBERS.
    PUBLIC = "public", "Public"


class WikiPageQuerySet(models.QuerySet):
    def visible_to(self, *, is_lead: bool, is_member: bool) -> "WikiPageQuerySet":
        """The pages a viewer may read.

        A lead reads every page; any other member every page but the
        leads-only ones; anyone else only the public ones. The caller is
        the one that knows the project is public before showing a
        non-member anything.
        """
        if is_lead:
            return self
        if is_member:
            return self.exclude(visibility=WikiVisibility.LEADS)
        return self.filter(visibility=WikiVisibility.PUBLIC)


class WikiPage(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="wiki_pages",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220)
    content_markdown = models.TextField(blank=True, default="")

    # A locked page is editable by project leads only. Use it for the pages
    # a project treats as settled — a protocol, a data dictionary — while
    # leaving the rest of the wiki open to every member.
    locked = models.BooleanField(
        default=False,
        help_text="Only project leads can edit a locked page.",
    )

    # A lead puts a page on the Project Overview's Wiki card, where members
    # see it first: the agreements and protocols everyone should read. Only
    # these pages are listed there.
    show_on_overview = models.BooleanField(
        default=False,
        help_text="Listed on the Project Overview's Wiki card.",
    )

    visibility = models.CharField(
        max_length=10,
        choices=WikiVisibility.choices,
        default=WikiVisibility.MEMBERS,
        help_text="Who can read the page.",
    )

    objects = WikiPageQuerySet.as_manager()

    # SET_NULL so a page survives the account that wrote it being removed;
    # authorship attribution degrades to "unknown" rather than taking the
    # documentation with it.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wiki_pages_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wiki_pages_updated",
    )

    class Meta:
        ordering = ["title", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"],
                name="uniq_wiki_page_slug_per_project",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "slug"], name="wiki_page_project_slug_idx"),
            models.Index(fields=["project", "-updated_at"], name="wiki_page_recent_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse(
            "wiki:detail",
            kwargs={"project_pk": self.project_id, "slug": self.slug},
        )

    @property
    def latest_revision_number(self) -> int:
        """The newest revision's number, or 0 for a page with no history.

        0 only happens for rows written outside ``apps.wiki.services`` (a
        fixture, a shell session); every page the app creates has at least
        revision 1.
        """
        latest = self.revisions.order_by("-number").first()
        return latest.number if latest else 0


class WikiRevision(models.Model):
    """One immutable snapshot of a page, written on every save."""

    page = models.ForeignKey(
        WikiPage,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    # 1-based and per-page, so a page's history reads "revision 4 of 7"
    # rather than exposing a global autoincrement.
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    content_markdown = models.TextField(blank=True, default="")
    # The editor's own note on what changed ("added the sampling protocol").
    summary = models.CharField(max_length=255, blank=True, default="")
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wiki_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "number"],
                name="uniq_wiki_revision_number_per_page",
            ),
        ]
        indexes = [
            models.Index(fields=["page", "-number"], name="wiki_rev_page_number_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.page_id} r{self.number}"

    def save(self, *args, **kwargs):
        """Revisions are append-only: an existing row cannot be rewritten.

        Deletion is *not* blocked — a page's history goes with the page when
        it is deleted (``on_delete=CASCADE``), and a history that outlived
        its page would be unreachable rather than useful.
        """
        if self.pk is not None:
            raise IntegrityError(
                "WikiRevision is append-only; existing rows cannot be updated."
            )
        super().save(*args, **kwargs)
