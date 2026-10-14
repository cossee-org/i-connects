"""Views for the wiki app.

Read access is project membership, enforced by ``ProjectContextMixin`` (403
for anyone else, including on a public project — the wiki is the project's
working documentation, not its public face).

Write access:
  * any member may create a page and edit an unlocked one;
  * a locked page is editable by project leads only;
  * a page may be deleted by a lead or by whoever created it.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import ListView, TemplateView, View
from django.views.generic.edit import FormView

from apps.tasks.mixins import ProjectContextMixin

from .forms import WikiPageForm
from .models import WikiPage, WikiRevision
from .services import (
    EditConflict,
    create_page,
    delete_page,
    link_resolver,
    restore_revision,
    update_page,
)
from .utils import diff_lines, excerpt, render_wiki_markdown, summarise_change

#: Index orderings the toggle offers. Alphabetical is the default: an index
#: that reshuffles every time somebody edits a page is hard to navigate.
SORTS = {
    "title": ["title", "id"],
    "recent": ["-updated_at", "id"],
}
DEFAULT_SORT = "title"


class WikiAccessMixin(LoginRequiredMixin, ProjectContextMixin):
    """Project membership plus the wiki's own permission questions."""

    def can_edit(self, page: WikiPage) -> bool:
        return self.is_project_manager or not page.locked

    def can_delete(self, page: WikiPage) -> bool:
        return self.is_project_manager or (
            page.created_by_id is not None
            and page.created_by_id == self.request.user.pk
        )

    def get_page(self) -> WikiPage:
        return get_object_or_404(
            WikiPage.objects.select_related("created_by", "updated_by"),
            project=self.project,
            slug=self.kwargs["slug"],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["wiki_page_count"] = WikiPage.objects.filter(
            project=self.project
        ).count()
        return context


class WikiPageListView(WikiAccessMixin, ListView):
    """The wiki index: every page, searchable."""

    template_name = "wiki/list.html"
    context_object_name = "pages"
    paginate_by = 25

    def get_queryset(self):
        self.query = (self.request.GET.get("q") or "").strip()
        self.sort = self.request.GET.get("sort")
        if self.sort not in SORTS:
            self.sort = DEFAULT_SORT
        queryset = WikiPage.objects.filter(project=self.project).select_related(
            "updated_by"
        )
        if self.query:
            queryset = queryset.filter(
                Q(title__icontains=self.query)
                | Q(content_markdown__icontains=self.query)
            )
        return queryset.order_by(*SORTS[self.sort])

    def get_template_names(self):
        # Typing in the search box swaps the results list, not the page.
        if self.request.headers.get("HX-Request"):
            return ["wiki/partials/page_results.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_q"] = self.query
        context["current_sort"] = self.sort
        context["rows"] = [
            {"page": page, "excerpt": excerpt(page.content_markdown)}
            for page in context["pages"]
        ]
        return context


class WikiPageDetailView(WikiAccessMixin, TemplateView):
    template_name = "wiki/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.get_page()
        rendered = render_wiki_markdown(
            page.content_markdown,
            resolve_link=link_resolver(self.project),
            with_toc=True,
        )
        latest = page.revisions.select_related("editor").first()
        context.update(
            {
                "page": page,
                "rendered": rendered,
                "latest_revision": latest,
                "revision_count": page.revisions.count(),
                "can_edit": self.can_edit(page),
                "can_delete": self.can_delete(page),
            }
        )
        return context


class WikiPageFormViewBase(WikiAccessMixin, FormView):
    template_name = "wiki/form.html"
    form_class = WikiPageForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["is_project_manager"] = self.is_project_manager
        return kwargs


class WikiPageCreateView(WikiPageFormViewBase):
    def get_initial(self):
        initial = super().get_initial()
        # A ``[[link]]`` to a page that does not exist yet lands here with
        # the title it was written as, so following the link and pressing
        # save is all it takes to fill the gap.
        title = (self.request.GET.get("title") or "").strip()
        if title:
            initial["title"] = title[:200]
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page"] = None
        return context

    def form_valid(self, form):
        page = create_page(
            project=self.project,
            actor=self.request.user,
            title=form.cleaned_data["title"],
            content_markdown=form.cleaned_data.get("content_markdown") or "",
            summary=form.cleaned_data.get("summary") or "",
            locked=bool(form.cleaned_data.get("locked")),
            show_on_overview=bool(form.cleaned_data.get("show_on_overview")),
        )
        messages.success(self.request, f"Created “{page.title}”.")
        return HttpResponseRedirect(page.get_absolute_url())


class WikiPageUpdateView(WikiPageFormViewBase):
    def get(self, request, *args, **kwargs):
        self._load_page()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._load_page()
        return super().post(request, *args, **kwargs)

    def _load_page(self) -> None:
        """Resolve the page and the caller's right to edit it.

        Called from the handlers rather than from ``dispatch`` so that
        ``ProjectContextMixin`` has already established membership — a
        non-member gets "not a member of this project", not "the page is
        locked".
        """
        self.page = self.get_page()
        if not self.can_edit(self.page):
            raise PermissionDenied(
                "This page is locked; only a project lead can edit it."
            )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.page
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        # Stamped from the page as loaded, so a save compares against the
        # revision this form was actually rendered from.
        initial["base_revision"] = self.page.latest_revision_number
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page"] = self.page
        return context

    def form_valid(self, form):
        try:
            page, _ = update_page(
                page=self.page,
                actor=self.request.user,
                title=form.cleaned_data["title"],
                content_markdown=form.cleaned_data.get("content_markdown") or "",
                summary=form.cleaned_data.get("summary") or "",
                locked=(
                    bool(form.cleaned_data.get("locked"))
                    if self.is_project_manager
                    else None
                ),
                show_on_overview=(
                    bool(form.cleaned_data.get("show_on_overview"))
                    if self.is_project_manager
                    else None
                ),
                expected_revision=form.cleaned_data.get("base_revision"),
            )
        except EditConflict as conflict:
            form.add_error(
                None,
                (
                    f"Someone else saved this page while you were editing it "
                    f"(you started from revision {conflict.expected}; it is now "
                    f"at revision {conflict.current}). Open the page history in "
                    f"another tab to see their change, then re-apply yours."
                ),
            )
            # Let the editor keep typing against the page as it now stands.
            form.data = form.data.copy()
            form.data[form.add_prefix("base_revision")] = conflict.current
            return self.form_invalid(form)
        messages.success(self.request, f"Saved “{page.title}”.")
        return HttpResponseRedirect(page.get_absolute_url())


class WikiPageDeleteView(WikiAccessMixin, TemplateView):
    """Confirm on GET, delete on POST."""

    template_name = "wiki/confirm_delete.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.get_page()
        self._require_delete_permission(page)
        context["page"] = page
        context["revision_count"] = page.revisions.count()
        return context

    def post(self, request, *args, **kwargs):
        page = self.get_page()
        self._require_delete_permission(page)
        title = page.title
        delete_page(page=page, actor=request.user)
        messages.success(request, f"Deleted \u201c{title}\u201d.")
        return HttpResponseRedirect(
            reverse("wiki:list", kwargs={"project_pk": self.project.pk})
        )

    def _require_delete_permission(self, page: WikiPage) -> None:
        if not self.can_delete(page):
            raise PermissionDenied(
                "Only a project lead or the page's author can delete it."
            )


class WikiPageHistoryView(WikiAccessMixin, TemplateView):
    template_name = "wiki/history.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.get_page()
        revisions = list(page.revisions.select_related("editor").order_by("-number"))
        # Each row is described against the revision below it, so the
        # history reads as "what this edit did" rather than as a list of
        # sizes.
        rows = []
        for index, revision in enumerate(revisions):
            previous = revisions[index + 1] if index + 1 < len(revisions) else None
            rows.append(
                {
                    "revision": revision,
                    "change": summarise_change(
                        previous.content_markdown if previous else "",
                        revision.content_markdown,
                    ),
                    "is_current": index == 0,
                }
            )
        context.update(
            {
                "page": page,
                "rows": rows,
                "can_edit": self.can_edit(page),
            }
        )
        return context


class WikiRevisionDetailView(WikiAccessMixin, TemplateView):
    template_name = "wiki/revision_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.get_page()
        revision = get_object_or_404(
            WikiRevision.objects.select_related("editor"),
            page=page,
            number=self.kwargs["number"],
        )
        previous = (
            page.revisions.filter(number__lt=revision.number)
            .order_by("-number")
            .first()
        )
        rendered = render_wiki_markdown(
            revision.content_markdown,
            resolve_link=link_resolver(self.project),
        )
        context.update(
            {
                "page": page,
                "revision": revision,
                "previous_revision": previous,
                "rendered": rendered,
                "diff": diff_lines(
                    previous.content_markdown if previous else "",
                    revision.content_markdown,
                ),
                "is_current": revision.number == page.latest_revision_number,
                "can_edit": self.can_edit(page),
            }
        )
        return context


class WikiRevisionRestoreView(WikiAccessMixin, View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        page = self.get_page()
        if not self.can_edit(page):
            raise PermissionDenied(
                "This page is locked; only a project lead can edit it."
            )
        revision = get_object_or_404(
            WikiRevision, page=page, number=self.kwargs["number"]
        )
        restore_revision(page=page, revision=revision, actor=request.user)
        messages.success(
            request, f"Restored revision {revision.number} of “{page.title}”."
        )
        return HttpResponseRedirect(page.get_absolute_url())
