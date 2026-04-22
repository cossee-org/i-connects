"""Views for the roles app (project-scoped role CRUD + assignment)."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import View

from apps.projects.mixins import (
    ProjectContextMixin,
    ProjectManagerRequiredMixin,
    ProjectMemberRequiredMixin,
)
from apps.projects.models import ProjectMember

from .forms import RoleForm
from .models import Role


def _roles_qs(project):
    return Role.objects.filter(project=project).order_by("name")


class RoleListView(ProjectMemberRequiredMixin, ProjectContextMixin, View):
    template_name = "roles/list.html"

    def get(self, request, project_pk):
        return render(
            request,
            self.template_name,
            {
                "project": self.project,
                "roles": _roles_qs(self.project),
                "new_form": RoleForm(),
                "members": ProjectMember.objects.filter(
                    project=self.project, deleted=False
                ).prefetch_related("roles", "users"),
            },
        )


class RoleCreateView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    """HTMX endpoint: creates a role and returns the refreshed list fragment."""

    http_method_names = ["post"]

    def post(self, request, project_pk):
        form = RoleForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "roles/form_row.html",
                {
                    "form": form,
                    "project": self.project,
                    "role": None,
                    "errors": True,
                },
                status=400,
            )
        role = form.save(commit=False)
        role.user = request.user
        role.project = self.project
        role.save()
        # Return a rendered row so the caller can append it to the list.
        return render(
            request,
            "roles/form_row.html",
            {
                "form": RoleForm(instance=role),
                "project": self.project,
                "role": role,
            },
        )


class RoleUpdateView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    http_method_names = ["get", "post"]

    def _get_role(self, pk):
        return get_object_or_404(Role, pk=pk, project=self.project)

    def get(self, request, project_pk, pk):
        role = self._get_role(pk)
        return render(
            request,
            "roles/form_row.html",
            {
                "form": RoleForm(instance=role),
                "project": self.project,
                "role": role,
                "editing": True,
            },
        )

    def post(self, request, project_pk, pk):
        role = self._get_role(pk)
        form = RoleForm(request.POST, instance=role)
        if not form.is_valid():
            return render(
                request,
                "roles/form_row.html",
                {
                    "form": form,
                    "project": self.project,
                    "role": role,
                    "editing": True,
                    "errors": True,
                },
                status=400,
            )
        role = form.save()
        return render(
            request,
            "roles/form_row.html",
            {
                "form": RoleForm(instance=role),
                "project": self.project,
                "role": role,
            },
        )


class RoleDeleteView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    http_method_names = ["post", "delete"]

    def post(self, request, project_pk, pk):
        return self._delete(request, pk)

    def delete(self, request, project_pk, pk):
        return self._delete(request, pk)

    def _delete(self, request, pk):
        role = get_object_or_404(Role, pk=pk, project=self.project)
        role.delete()
        if request.headers.get("HX-Request"):
            return HttpResponse("")
        return redirect(reverse("roles:list", kwargs={"project_pk": self.project.pk}))


class AssignRolesView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    """Assign/unassign roles on a ProjectMember via multi-select POST."""

    http_method_names = ["post"]

    def post(self, request, project_pk, member_pk):
        member = get_object_or_404(
            ProjectMember, pk=member_pk, project=self.project
        )
        role_ids = request.POST.getlist("roles")
        roles = Role.objects.filter(
            pk__in=role_ids, project=self.project
        )
        member.roles.set(roles)
        if request.headers.get("HX-Request"):
            return render(
                request,
                "roles/_member_roles.html",
                {"member": member, "project": self.project},
            )
        return redirect(reverse("roles:list", kwargs={"project_pk": self.project.pk}))
