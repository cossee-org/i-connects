"""Shared view mixins for the tasks app.

Provides:
- ``ProjectContextMixin`` — resolves the active ``Project`` and the current
  user's ``ProjectMember`` record from URL kwargs. Raises 403 if the user is
  not a member of the project.
- ``TaskMemberAccessMixin`` — for task-scoped URLs, loads the ``Task`` and
  verifies the current user is a member of the task's project.
- ``ProjectManagerRequiredMixin`` — further restricts access to users who
  have a ``ProjectPrivilege`` of ``PROJECT_MANAGER`` on the project.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404

from apps.core.enums import MemberPrivileges
from apps.projects.models import Project, ProjectMember, ProjectPrivilege
from apps.tasks.models import Task


class ProjectContextMixin:
    """Resolve project and membership from URL kwargs.

    Expects ``project_pk`` in the URL kwargs. Sets ``self.project`` and
    ``self.project_member`` (the caller's membership record, or ``None``
    if the caller is a project manager without an explicit ProjectMember
    row — uncommon but possible).
    """

    project_url_kwarg = "project_pk"

    def dispatch(self, request, *args, **kwargs):  # type: ignore[override]
        project_pk = kwargs.get(self.project_url_kwarg)
        self.project = get_object_or_404(Project, pk=project_pk)
        self.project_member = self._resolve_member(request.user, self.project)
        self.is_project_manager = self._is_project_manager(request.user, self.project)
        if self.project_member is None and not self.is_project_manager:
            raise PermissionDenied("You are not a member of this project.")
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _resolve_member(user, project) -> ProjectMember | None:
        if not user.is_authenticated:
            return None
        return (
            ProjectMember.objects.filter(
                project=project,
                users=user,
                deleted=False,
                name__isnull=True,  # individual membership (team rows have name)
            )
            .order_by("id")
            .first()
        )

    @staticmethod
    def _is_project_manager(user, project) -> bool:
        if not user.is_authenticated:
            return False
        return ProjectPrivilege.objects.filter(
            user=user,
            project=project,
            privilege=MemberPrivileges.PROJECT_MANAGER,
        ).exists()

    def get_context_data(self, **kwargs):  # type: ignore[override]
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["project_member"] = self.project_member
        context["is_project_manager"] = self.is_project_manager
        # Drives the project section tabs in the top bar (members only).
        context["is_project_member"] = (
            self.project_member is not None or self.is_project_manager
        )
        return context


class TaskMemberAccessMixin:
    """Load a Task from ``pk`` and enforce project membership.

    Use on task-scoped URLs that do NOT include ``project_pk`` (e.g. the
    HTMX move endpoint). For URLs that include ``project_pk``, prefer
    composing ``ProjectContextMixin`` instead.
    """

    task_url_kwarg = "pk"

    def dispatch(self, request, *args, **kwargs):  # type: ignore[override]
        task_pk = kwargs.get(self.task_url_kwarg)
        self.task = get_object_or_404(Task.objects.select_related("project"), pk=task_pk)
        self.project = self.task.project
        self.project_member = ProjectContextMixin._resolve_member(request.user, self.project)
        self.is_project_manager = ProjectContextMixin._is_project_manager(
            request.user, self.project
        )
        if self.project_member is None and not self.is_project_manager:
            raise PermissionDenied("You are not a member of this project.")
        return super().dispatch(request, *args, **kwargs)


class ProjectManagerRequiredMixin:
    """Restrict a view to Project Leads (``PROJECT_MANAGER`` privilege).

    Compose *before* ``ProjectContextMixin`` / ``TaskMemberAccessMixin`` in
    the base-class list (so this ``dispatch`` runs first); it resolves the
    project itself, so it's safe regardless of MRO. Members without the
    privilege get a clean ``403`` — which both full-page navigation and HTMX
    handle correctly (htmx won't swap a non-2xx response).
    """

    def dispatch(self, request, *args, **kwargs):  # type: ignore[override]
        project = getattr(self, "project", None)
        if project is None:
            project_pk = kwargs.get("project_pk")
            if project_pk is not None:
                project = get_object_or_404(Project, pk=project_pk)
            elif "pk" in kwargs and "log_pk" not in kwargs:
                # Task-scoped URL: resolve via Task.
                task = Task.objects.select_related("project").filter(pk=kwargs["pk"]).first()
                if task is not None:
                    project = task.project
        if project is None or not ProjectContextMixin._is_project_manager(
            request.user, project
        ):
            return HttpResponseForbidden(
                "Only a Project Lead can perform this action."
            )
        return super().dispatch(request, *args, **kwargs)
