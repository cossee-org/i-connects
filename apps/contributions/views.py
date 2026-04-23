"""Views for the contributions app.

Renders the per-project contributions page: a members x CRediT-type matrix
and a recent-activity log. Reuses ``ProjectContextMixin`` for project
resolution and membership enforcement.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.tasks.mixins import ProjectContextMixin

from .services import get_project_contributions, get_project_contributions_matrix


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
