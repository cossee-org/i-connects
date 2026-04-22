"""Authorization mixins for project-scoped views."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from apps.core.enums import MemberPrivileges

from .models import Project, ProjectPrivilege


def _get_project_pk(view) -> int:
    """Pull project pk from URL kwargs, handling both ``pk`` and ``project_pk``."""
    kwargs = view.kwargs
    if "project_pk" in kwargs:
        return int(kwargs["project_pk"])
    if "pk" in kwargs:
        return int(kwargs["pk"])
    raise PermissionDenied("No project primary key supplied.")


class ProjectContextMixin:
    """Loads ``self.project`` from the URL and exposes it to the template."""

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(Project, pk=_get_project_pk(self))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["user_privilege"] = _get_user_privilege(self.request.user, self.project)
        return context


def _get_user_privilege(user, project) -> str | None:
    if not user.is_authenticated:
        return None
    priv = (
        ProjectPrivilege.objects.filter(user=user, project=project)
        .order_by("-created_at")
        .first()
    )
    return priv.privilege if priv else None


class ProjectMemberRequiredMixin(LoginRequiredMixin):
    """User must have at least a CONTRIBUTOR privilege on the project."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        project_pk = _get_project_pk(self)
        if not ProjectPrivilege.objects.filter(
            user=request.user, project_id=project_pk
        ).exists():
            raise PermissionDenied("You are not a member of this project.")
        return super().dispatch(request, *args, **kwargs)


class ProjectManagerRequiredMixin(LoginRequiredMixin):
    """User must have PROJECT_MANAGER privilege on the project."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        project_pk = _get_project_pk(self)
        is_manager = ProjectPrivilege.objects.filter(
            user=request.user,
            project_id=project_pk,
            privilege=MemberPrivileges.PROJECT_MANAGER,
        ).exists()
        if not is_manager:
            raise PermissionDenied("Project manager privilege required.")
        return super().dispatch(request, *args, **kwargs)
