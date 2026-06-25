"""Views for the contributions app.

Renders the per-project contributions page (matrix + recent activity) and
the CSV export endpoints for authorship-statement preparation.
"""

from __future__ import annotations

import csv

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView

from apps.core.enums import ContributionType
from apps.projects.models import Project
from apps.tasks.mixins import ProjectContextMixin

from .services import (
    get_project_contributions,
    get_project_contributions_matrix,
    get_user_contribution_counts_by_type,
    get_user_contributions,
)


class ContributionsView(LoginRequiredMixin, TemplateView):
    """Top-level contributions page (placeholder)."""

    template_name = "contributions/contributions.html"


class MyContributionsView(LoginRequiredMixin, TemplateView):
    """Dedicated page for a user's own contributions across all projects."""

    template_name = "contributions/my_contributions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["contribution_counts"] = get_user_contribution_counts_by_type(
            self.request.user
        )
        context["contribution_types"] = ContributionType.choices
        context["recent_contributions"] = get_user_contributions(self.request.user).order_by("-closed_at")[:10]
        context["contribution_total"] = sum(context["contribution_counts"].values())
        return context


class ProjectContributionsView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """Contributions page for a single project.

    Accessible to any authenticated user who is a member of the project
    (or a project manager). ``ProjectContextMixin`` raises 403 otherwise.
    """

    template_name = "contributions/project_contributions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["matrix"] = get_project_contributions_matrix(self.project)
        context["recent"] = (
            get_project_contributions(self.project).order_by("-closed_at")[:50]
        )
        return context


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
