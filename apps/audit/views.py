"""Views for the audit log — user-facing list pages.

Two read-only list views:

- ``MyAuditView``: shows every ``AuditLog`` row where the caller is the
  actor. Available to any authenticated user (GDPR: users can read their
  own audit entries).
- ``ProjectAuditView``: shows every ``AuditLog`` row scoped to a single
  project. Restricted to project managers — non-PM members get 403.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.generic import ListView

from apps.audit.models import AuditLog
from apps.projects.models import Project
from apps.tasks.mixins import ProjectContextMixin


class MyAuditView(LoginRequiredMixin, ListView):
    template_name = "audit/my_activity.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        return (
            AuditLog.objects.filter(actor=self.request.user)
            .select_related("project", "actor")
        )


class ProjectAuditView(LoginRequiredMixin, ProjectContextMixin, ListView):
    template_name = "audit/project_activity.html"
    context_object_name = "entries"
    paginate_by = 50

    def dispatch(self, request, *args, **kwargs):
        # Resolve project + PM flag before delegating — we need to refuse
        # non-PMs with 403 rather than fall through to the membership
        # PermissionDenied that ``ProjectContextMixin`` would raise.
        self.project = get_object_or_404(
            Project, pk=kwargs.get(self.project_url_kwarg)
        )
        self.project_member = ProjectContextMixin._resolve_member(
            request.user, self.project
        )
        self.is_project_manager = ProjectContextMixin._is_project_manager(
            request.user, self.project
        )
        if not self.is_project_manager:
            return HttpResponseForbidden(
                "Only project managers may view this activity log."
            )
        # Skip ProjectContextMixin.dispatch (we've already done its work)
        # and go straight to ListView.dispatch.
        return ListView.dispatch(self, request, *args, **kwargs)

    def get_queryset(self):
        return AuditLog.objects.filter(project=self.project).select_related("actor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["is_project_manager"] = self.is_project_manager
        return context
