"""Views for the forms_builder app.

Covers three surfaces:

* Managing Form templates (CRUD + listing).
* Managing FormVersion rows (create/view/clone/archive).
* Attaching a FormVersion to a Project/Task and rendering the resulting
  dynamic form for end-users to fill in.
"""

from __future__ import annotations

import json
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.core.enums import MemberPrivileges
from apps.projects.models import Project, ProjectMember, ProjectPrivilege
from apps.tasks.models import Task

from .forms import AttachFormForm, FormMetaForm, FormVersionForm
from .models import Form, FormVersion
from .schema_to_form import build_form_class
from .services import (
    archive_form,
    archive_version,
    clone_version,
    create_form_with_initial_version,
    create_version,
)
from .validators import validate_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_form_or_404(user, pk: int) -> Form:
    form = get_object_or_404(Form, pk=pk)
    if form.user_id != user.id:
        raise Http404
    return form


def _is_project_manager(*, user, project: Project) -> bool:
    return ProjectPrivilege.objects.filter(
        user=user,
        project=project,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists()


def _is_project_member(*, user, project: Project) -> bool:
    return ProjectMember.objects.filter(
        project=project, users=user, deleted=False
    ).exists()


def _user_form_versions_queryset(user):
    return (
        FormVersion.objects.filter(
            form__user=user, form__archived=False, archived=False
        )
        .select_related("form")
        .order_by("-form_id", "-version")
    )


# ---------------------------------------------------------------------------
# Form CRUD
# ---------------------------------------------------------------------------


@login_required
def form_list(request: HttpRequest) -> HttpResponse:
    forms_qs = (
        Form.objects.filter(user=request.user, archived=False)
        .prefetch_related("versions")
        .order_by("-updated_at")
    )
    rows = []
    for form in forms_qs:
        latest = form.versions.order_by("-version").first()
        rows.append({"form": form, "latest": latest})
    return render(request, "forms_builder/list.html", {"rows": rows})


@login_required
@require_http_methods(["GET", "POST"])
def form_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        name = (request.POST.get("display_name") or "Untitled form").strip()
        form = create_form_with_initial_version(
            user=request.user,
            name=name,
            json_schema={"title": name, "type": "object", "properties": {}},
        )
        messages.success(request, "Survey created.")
        return redirect("forms_builder:version_list", pk=form.pk)
    return render(request, "forms_builder/form_meta_form.html", {"mode": "create"})


@login_required
@require_http_methods(["GET", "POST"])
def form_update(request: HttpRequest, pk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    if request.method == "POST":
        meta_form = FormMetaForm(request.POST, instance=form_obj)
        if meta_form.is_valid():
            meta_form.save()
            display_name = meta_form.cleaned_data.get("display_name")
            if display_name:
                latest = form_obj.versions.order_by("-version").first()
                if latest is not None and latest.name != display_name:
                    latest.name = display_name
                    latest.save(update_fields=["name"])
            messages.success(request, "Survey updated.")
            return redirect("forms_builder:form_list")
    else:
        meta_form = FormMetaForm(instance=form_obj)
    return render(
        request,
        "forms_builder/form_meta_form.html",
        {"form": meta_form, "form_obj": form_obj, "mode": "edit"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def form_delete(request: HttpRequest, pk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    if request.method == "POST":
        archive_form(form=form_obj)
        messages.success(request, "Survey archived.")
        return redirect("forms_builder:form_list")
    return render(
        request,
        "forms_builder/confirm_delete.html",
        {"form_obj": form_obj},
    )


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------


@login_required
def version_list(request: HttpRequest, pk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    versions = form_obj.versions.order_by("-version")
    return render(
        request,
        "forms_builder/version_list.html",
        {"form_obj": form_obj, "versions": versions},
    )


@login_required
@require_http_methods(["GET", "POST"])
def version_create(request: HttpRequest, pk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    latest = form_obj.versions.order_by("-version").first()
    if request.method == "POST":
        vform = FormVersionForm(request.POST)
        if vform.is_valid():
            version = create_version(
                form=form_obj,
                json_schema=vform.cleaned_data["json_schema"],
                ui_schema=vform.cleaned_data.get("ui_schema"),
                name=vform.cleaned_data.get("name") or None,
            )
            messages.success(request, f"Version {version.version} created.")
            return redirect(
                "forms_builder:version_detail", pk=form_obj.pk, vpk=version.pk
            )
    else:
        initial: dict[str, Any] = {}
        if latest is not None:
            initial["name"] = latest.name
            initial["json_schema"] = json.dumps(latest.json_schema or {}, indent=2)
            if latest.ui_schema is not None:
                initial["ui_schema"] = json.dumps(latest.ui_schema, indent=2)
        else:
            initial["json_schema"] = json.dumps(
                {"title": "New form", "type": "object", "properties": {}}, indent=2
            )
        vform = FormVersionForm(initial=initial)
    return render(
        request,
        "forms_builder/version_form.html",
        {"form": vform, "form_obj": form_obj},
    )


@login_required
def version_detail(request: HttpRequest, pk: int, vpk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    version = get_object_or_404(FormVersion, pk=vpk, form=form_obj)
    try:
        rendered_form = build_form_class(version.json_schema or {})()
    except Exception as exc:  # pragma: no cover - defensive
        rendered_form = None
        render_error = str(exc)
    else:
        render_error = None
    return render(
        request,
        "forms_builder/version_detail.html",
        {
            "form_obj": form_obj,
            "version": version,
            "pretty_schema": json.dumps(version.json_schema or {}, indent=2),
            "pretty_ui_schema": json.dumps(version.ui_schema or {}, indent=2),
            "rendered_form": rendered_form,
            "render_error": render_error,
        },
    )


@login_required
@require_http_methods(["POST"])
def version_clone(request: HttpRequest, pk: int, vpk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    version = get_object_or_404(FormVersion, pk=vpk, form=form_obj)
    new_version = clone_version(source=version)
    messages.success(request, f"Cloned to version {new_version.version}.")
    return redirect("forms_builder:version_detail", pk=form_obj.pk, vpk=new_version.pk)


@login_required
@require_http_methods(["POST"])
def version_archive(request: HttpRequest, pk: int, vpk: int) -> HttpResponse:
    form_obj = _user_form_or_404(request.user, pk)
    version = get_object_or_404(FormVersion, pk=vpk, form=form_obj)
    archive_version(version=version)
    messages.success(request, f"Version {version.version} archived.")
    return redirect("forms_builder:version_list", pk=form_obj.pk)


# ---------------------------------------------------------------------------
# Attaching a form version to a Project / Task
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def project_forms(request: HttpRequest, project_pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=project_pk)
    if not _is_project_manager(user=request.user, project=project):
        raise Http404

    if request.method == "POST":
        attach_form = AttachFormForm(request.POST, user=request.user)
        if attach_form.is_valid():
            project.form_version = attach_form.cleaned_data["form_version"]
            project.save(update_fields=["form_version", "updated_at"])
            messages.success(request, "Survey attached to project.")
            return redirect("forms_builder:project_forms", project_pk=project.pk)
    else:
        attach_form = AttachFormForm(user=request.user)

    return render(
        request,
        "forms_builder/project_forms.html",
        {
            "project": project,
            "attach_form": attach_form,
            "current_version": project.form_version,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def project_fill(request: HttpRequest, project_pk: int) -> HttpResponse:
    project = get_object_or_404(Project, pk=project_pk)
    if not _is_project_member(user=request.user, project=project):
        raise Http404
    version = project.form_version
    if version is None:
        messages.error(request, "No survey is attached to this project.")
        return redirect("forms_builder:project_forms", project_pk=project.pk)

    schema = version.json_schema or {}
    DynamicForm = build_form_class(schema)
    existing = (project.metadata or {}).get("form_data", {}) if project.metadata else {}

    if request.method == "POST":
        dyn = DynamicForm(request.POST)
        if dyn.is_valid():
            schema_errors = validate_data(schema, dyn.cleaned_data)
            if schema_errors:
                for err in schema_errors:
                    dyn.add_error(None, err)
            else:
                metadata = dict(project.metadata or {})
                metadata["form_data"] = dyn.cleaned_data
                project.metadata = metadata
                project.save(update_fields=["metadata", "updated_at"])
                messages.success(request, "Survey response saved.")
                return redirect("forms_builder:project_fill", project_pk=project.pk)
    else:
        dyn = DynamicForm(initial=existing or None)

    return render(
        request,
        "forms_builder/fill_form.html",
        {
            "owner_kind": "project",
            "owner": project,
            "version": version,
            "dyn_form": dyn,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def task_fill(request: HttpRequest, task_pk: int) -> HttpResponse:
    task = get_object_or_404(Task, pk=task_pk)
    if not _is_project_member(user=request.user, project=task.project):
        raise Http404
    version = task.form_version
    if version is None:
        messages.error(request, "No survey is attached to this task.")
        return redirect("forms_builder:project_forms", project_pk=task.project_id)

    schema = version.json_schema or {}
    DynamicForm = build_form_class(schema)
    # Task has no dedicated JSON column in the current schema -- piggyback on
    # the ``tags`` JSONField to store submissions without migrating the tasks
    # app. Submissions live under the 'form_data' key.
    existing = (task.tags or {}).get("form_data", {}) if isinstance(task.tags, dict) else {}

    if request.method == "POST":
        dyn = DynamicForm(request.POST)
        if dyn.is_valid():
            schema_errors = validate_data(schema, dyn.cleaned_data)
            if schema_errors:
                for err in schema_errors:
                    dyn.add_error(None, err)
            else:
                tags = dict(task.tags) if isinstance(task.tags, dict) else {}
                tags["form_data"] = dyn.cleaned_data
                task.tags = tags
                task.save(update_fields=["tags", "updated_at"])
                messages.success(request, "Survey response saved.")
                return redirect("forms_builder:task_fill", task_pk=task.pk)
    else:
        dyn = DynamicForm(initial=existing or None)

    return render(
        request,
        "forms_builder/fill_form.html",
        {
            "owner_kind": "task",
            "owner": task,
            "version": version,
            "dyn_form": dyn,
        },
    )
