"""Views for the contributions app.

Renders the Contributions module — a person's own credit across every
project they work on — and the CSV exports that feed an authorship
statement. A project's own ledger lives on its Contribution Tracking tab
(``projects.views.ContributionTrackingView``).
"""

from __future__ import annotations

import csv

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import RedirectView, TemplateView

from apps.core.enums import CREDIT_ROLES, ContributionType
from apps.projects.models import Project
from apps.tasks.mixins import ProjectContextMixin

from .services import (
    get_project_contributions,
    get_user_contribution_counts_by_type,
    get_user_contributions,
    get_user_credit_projects,
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
# CSV export (Ultra Plan §15: feeds authorship / CRediT statements)
# ---------------------------------------------------------------------------

CSV_COLUMNS = [
    "project",
    "user_name",
    "user_email",
    "user_orcid",
    "contribution_type",
    "task",
    "closed_at",
    "verified_by",
]


def _serialize_row(record) -> list[str]:
    user = record.user
    verifier = record.verified_by
    return [
        record.project.name if record.project else "",
        user.name or "",
        user.email,
        getattr(user, "orcid", "") or "",
        record.get_contribution_type_display(),
        record.task.name if record.task else "",
        record.closed_at.isoformat() if record.closed_at else "",
        (verifier.name or verifier.email) if verifier else "",
    ]


def _csv_response(filename: str, rows_iter) -> StreamingHttpResponse:
    """Stream a CSV without buffering the whole sheet in memory."""

    class _Echo:
        def write(self, value):
            return value

    writer = csv.writer(_Echo())

    def generate():
        yield writer.writerow(CSV_COLUMNS)
        for row in rows_iter:
            yield writer.writerow(row)

    response = StreamingHttpResponse(generate(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class ProjectContributionsCSVView(LoginRequiredMixin, ProjectContextMixin, View):
    """Download every ContributionRecord for a project as CSV.

    Restricted to project managers — the raw sheet contains other members'
    emails and ORCIDs, which ordinary members shouldn't re-download in
    bulk. The matrix page (``ProjectContributionsView``) remains open to
    all project members.
    """

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project, pk=kwargs.get(self.project_url_kwarg)
        )
        self.is_project_manager = ProjectContextMixin._is_project_manager(
            request.user, self.project
        )
        if not self.is_project_manager:
            return HttpResponseForbidden(
                "Only project managers may export contributions."
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        qs = get_project_contributions(self.project).order_by("-closed_at")
        stamp = timezone.now().strftime("%Y%m%d")
        filename = f"contributions-{slugify(self.project.name)}-{stamp}.csv"
        return _csv_response(filename, (_serialize_row(r) for r in qs.iterator()))


class MyContributionsCSVView(LoginRequiredMixin, View):
    """Download the signed-in user's own contributions across every project."""

    def get(self, request, *args, **kwargs):
        qs = get_user_contributions(request.user).order_by("-closed_at")
        stamp = timezone.now().strftime("%Y%m%d")
        filename = f"my-contributions-{stamp}.csv"
        return _csv_response(filename, (_serialize_row(r) for r in qs.iterator()))
