"""Views for the contributions app.

Renders the Contributions module — a person's own credit across every
project they work on — and the CSV exports that feed an authorship
statement. A project's own ledger lives on its Contribution Tracking tab
(``projects.views.ContributionTrackingView``).
"""

from __future__ import annotations

import csv
from itertools import chain

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import RedirectView, TemplateView

from apps.core.enums import CREDIT_ROLES, ContributionType, MemberPrivileges
from apps.projects.models import Project, ProjectPrivilege

from .forms import ContributionAddForm, ContributionEditForm
from .models import ContributionRecord
from .services import (
    add_contribution,
    get_project_contributions,
    get_project_contributions_matrix,
    get_user_contribution_counts_by_type,
    get_user_contributions,
    get_user_contributions_matrix,
    get_user_credit_projects,
    remove_contribution,
    update_contribution,
    user_display_name,
)


class ContributionsView(LoginRequiredMixin, TemplateView):
    """The Contributions module: a person's own credit, across every project.

    ``?project=<pk>`` narrows it to one. An unreadable or unknown pk falls
    back to all projects rather than 404ing — a stale bookmark should still
    show you your contributions.
    """

    template_name = "contributions/contributions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        projects = get_user_credit_projects(user)
        selected = self._selected_project(projects)

        counts = get_user_contribution_counts_by_type(user, project=selected)
        records = get_user_contributions(user, project=selected).order_by(
            "-closed_at", "-id"
        )

        context["filter_projects"] = projects
        context["selected_project"] = selected
        context["current_project"] = str(selected.pk) if selected else ""
        context["records"] = records
        context["contribution_total"] = sum(counts.values())
        # Only the roles actually earned, in the taxonomy's own order —
        # fourteen zeroed tiles say nothing the total does not.
        labels = dict(ContributionType.choices)
        context["counts_by_type"] = [
            (labels.get(value, value), counts[value])
            for value in list(CREDIT_ROLES) + sorted(
                v for v in counts if v not in CREDIT_ROLES
            )
            if counts.get(value)
        ]
        return context

    def _selected_project(self, projects):
        raw = self.request.GET.get("project") or ""
        if not raw.isdigit():
            return None
        pk = int(raw)
        return next((p for p in projects if p.pk == pk), None)


class MyContributionsView(LoginRequiredMixin, RedirectView):
    """Old address for a person's own contributions.

    The Contributions module in the sidebar is the one page now, so this
    redirects rather than keeping a second copy in step with it.
    """

    permanent = False
    pattern_name = "contributions:contributions"


class ProjectContributionsView(LoginRequiredMixin, RedirectView):
    """Old address for a project's contributions.

    The project's own Contribution Tracking tab is where they live now, so
    the section nav stays lit while you read them. Kept as a redirect so
    links already handed out still land somewhere.
    """

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse(
            "projects:contribution_tracking",
            kwargs={"pk": kwargs["project_pk"]},
        )


# ---------------------------------------------------------------------------
# Corrections — lead-only edits to the ledger
# ---------------------------------------------------------------------------


class ProjectLeadRequiredMixin(LoginRequiredMixin):
    """Resolve ``self.project`` from ``project_pk`` and require a lead on it."""

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=kwargs.get("project_pk"))
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        is_lead = ProjectPrivilege.objects.filter(
            user=request.user,
            project=self.project,
            privilege=MemberPrivileges.PROJECT_MANAGER,
        ).exists()
        if not is_lead:
            return HttpResponseForbidden(
                "Only project leads may correct contributions."
            )
        return super().dispatch(request, *args, **kwargs)

    def _tracking_url(self) -> str:
        return reverse(
            "projects:contribution_tracking", kwargs={"pk": self.project.pk}
        )


class ContributionAddView(ProjectLeadRequiredMixin, View):
    """Credit someone on a task the automatic writer passed over."""

    def get(self, request, *args, **kwargs):
        return self._render(ContributionAddForm(project=self.project))

    def post(self, request, *args, **kwargs):
        form = ContributionAddForm(request.POST, project=self.project)
        if not form.is_valid():
            return self._render(form, status=400)
        add_contribution(
            task=form.cleaned_data["task"],
            user=form.cleaned_data["user"],
            contribution_type=form.cleaned_data["contribution_type"],
            actor=request.user,
        )
        messages.success(request, "Contribution added.")
        return _refresh(request, self._tracking_url())

    def _render(self, form, *, status=200):
        return render(
            self.request,
            "contributions/partials/add_form.html",
            {"form": form, "project": self.project},
            status=status,
        )


class ContributionEditView(ProjectLeadRequiredMixin, View):
    """Correct the role one contribution is recorded under."""

    def get_record(self):
        return get_object_or_404(
            ContributionRecord.objects.select_related("task", "user"),
            pk=self.kwargs["pk"],
            project=self.project,
        )

    def get(self, request, *args, **kwargs):
        record = self.get_record()
        return self._render(record, ContributionEditForm(record=record))

    def post(self, request, *args, **kwargs):
        record = self.get_record()
        form = ContributionEditForm(request.POST, record=record)
        if not form.is_valid():
            return self._render(record, form, status=400)
        update_contribution(
            record,
            contribution_type=form.cleaned_data["contribution_type"],
            actor=request.user,
        )
        messages.success(request, "Contribution updated.")
        return _refresh(request, self._tracking_url())

    def _render(self, record, form, *, status=200):
        return render(
            self.request,
            "contributions/partials/edit_form.html",
            {"form": form, "record": record, "project": self.project},
            status=status,
        )


class ContributionRemoveView(ProjectLeadRequiredMixin, View):
    """Withdraw a contribution a lead judges shouldn't stand."""

    def post(self, request, *args, **kwargs):
        record = get_object_or_404(
            ContributionRecord, pk=kwargs["pk"], project=self.project
        )
        remove_contribution(record, actor=request.user)
        messages.success(request, "Contribution withdrawn.")
        return _refresh(request, self._tracking_url())


def _refresh(request, url: str):
    """Send the caller back to the tracking tab, HTMX or not."""
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return HttpResponseRedirect(url)


# ---------------------------------------------------------------------------
# CSV downloads — the sheets an authorship statement is written from
#
# Two shapes, per contributions surface:
#   list   — one row per contribution, with everything recorded about it.
#   matrix — the grid the page itself shows: one row per member (or, for a
#            person's own download, per project) and one column per CRediT
#            role, counting the contributions in each cell.
# ---------------------------------------------------------------------------

LIST_COLUMNS = [
    "project",
    "user_name",
    "user_email",
    "user_orcid",
    "contribution_type",
    "task",
    "closed_at",
    "verified_by",
]

#: ``?format=`` values. Anything else falls back to the list.
FORMAT_MATRIX = "matrix"


def _wants_matrix(request) -> bool:
    return (request.GET.get("format") or "").lower() == FORMAT_MATRIX


def _list_row(record) -> list[str]:
    user = record.user
    verifier = record.verified_by
    return [
        record.project.name if record.project else "",
        user_display_name(user),
        user.email,
        getattr(user, "orcid", "") or "",
        record.get_contribution_type_display(),
        record.task.name if record.task else "",
        record.closed_at.isoformat() if record.closed_at else "",
        user_display_name(verifier) if verifier else "",
    ]


def _matrix_rows(matrix: dict, *, first_column: str, label_for) -> list[list[str]]:
    """The matrix as CSV rows: a header, a row per entry, then the totals."""
    labels = [label for _value, label in matrix["types"]]
    rows = [[first_column, *labels, "total"]]
    for row in matrix["rows"]:
        rows.append(
            [
                label_for(row),
                *[str(row["counts"].get(value, 0)) for value, _ in matrix["types"]],
                str(row["total"]),
            ]
        )
    rows.append(
        [
            "total",
            *[str(matrix["totals"].get(value, 0)) for value, _ in matrix["types"]],
            str(matrix["totals"]["grand_total"]),
        ]
    )
    return rows


def _csv_response(filename: str, rows_iter) -> StreamingHttpResponse:
    """Stream a CSV without buffering the whole sheet in memory.

    ``rows_iter`` yields every row including the header, so the same helper
    serves the list export (a fixed header over a queryset) and the matrix
    (a header whose columns depend on the data).
    """

    class _Echo:
        def write(self, value):
            return value

    writer = csv.writer(_Echo())

    def generate():
        for row in rows_iter:
            yield writer.writerow(row)

    response = StreamingHttpResponse(generate(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _stamp() -> str:
    return timezone.now().strftime("%Y%m%d")


class ProjectContributionsCSVView(LoginRequiredMixin, View):
    """Download a project's contributions, as a list or as the matrix.

    The matrix is open to every project member: it is exactly the grid the
    Contribution Tracking tab already shows them, so it reads membership
    the same way that page does. The list is restricted to project leads —
    it carries other members' emails and ORCIDs, which ordinary members
    shouldn't re-download in bulk.
    """

    project_url_kwarg = "project_pk"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project, pk=kwargs.get(self.project_url_kwarg)
        )
        privileges = ProjectPrivilege.objects.filter(
            user=request.user.pk, project=self.project
        )
        self.is_project_manager = privileges.filter(
            privilege=MemberPrivileges.PROJECT_MANAGER
        ).exists()
        if not privileges.exists():
            return HttpResponseForbidden(
                "Only project members may download contributions."
            )
        if not _wants_matrix(request) and not self.is_project_manager:
            return HttpResponseForbidden(
                "Only project leads may download the full contribution list."
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        slug = slugify(self.project.name)
        if _wants_matrix(request):
            matrix = get_project_contributions_matrix(self.project)
            rows = _matrix_rows(
                matrix,
                first_column="member",
                label_for=lambda row: user_display_name(row["user"]),
            )
            return _csv_response(
                f"contributions-{slug}-matrix-{_stamp()}.csv", iter(rows)
            )

        qs = get_project_contributions(self.project).order_by("-closed_at", "-id")
        rows = chain([LIST_COLUMNS], (_list_row(r) for r in qs.iterator()))
        return _csv_response(f"contributions-{slug}-list-{_stamp()}.csv", rows)


class MyContributionsCSVView(LoginRequiredMixin, View):
    """Download the signed-in user's own contributions, list or matrix.

    Honours the module's ``?project=`` filter, so what you downloaded is
    what you were looking at.
    """

    def get(self, request, *args, **kwargs):
        project = self._selected_project(request)
        scope = f"-{slugify(project.name)}" if project else ""

        if _wants_matrix(request):
            matrix = get_user_contributions_matrix(
                request.user, project=project, all_types=True
            )
            rows = _matrix_rows(
                matrix,
                first_column="project",
                label_for=lambda row: row["project"].name,
            )
            return _csv_response(
                f"my-contributions{scope}-matrix-{_stamp()}.csv", iter(rows)
            )

        qs = get_user_contributions(request.user, project=project).order_by(
            "-closed_at", "-id"
        )
        rows = chain([LIST_COLUMNS], (_list_row(r) for r in qs.iterator()))
        return _csv_response(f"my-contributions{scope}-list-{_stamp()}.csv", rows)

    @staticmethod
    def _selected_project(request):
        """The filtered project, or None — same rule as the module page."""
        raw = request.GET.get("project") or ""
        if not raw.isdigit():
            return None
        return next(
            (p for p in get_user_credit_projects(request.user) if p.pk == int(raw)),
            None,
        )
