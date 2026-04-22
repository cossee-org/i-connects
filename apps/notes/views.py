"""Views for the notes app."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from apps.core.enums import MemberPrivileges, NoteVisibility
from apps.projects.models import Project, ProjectMember, ProjectPrivilege

from .forms import NoteForm
from .models import Note
from .utils import render_markdown


# ---------------------------------------------------------------------------
# Authorisation helpers
# ---------------------------------------------------------------------------
def _require_project_member(user, project: Project) -> ProjectMember:
    """Ensure the user is a member of the project; return their ProjectMember.

    Raises :class:`PermissionDenied` if not a member.
    """
    if not user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    member = (
        ProjectMember.objects.filter(project=project, users=user, deleted=False)
        .order_by("id")
        .first()
    )
    if member is None:
        raise PermissionDenied("You are not a member of this project.")
    return member


def _is_project_manager(user, project: Project) -> bool:
    if not user.is_authenticated:
        return False
    return ProjectPrivilege.objects.filter(
        user=user,
        project=project,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists()


def _visible_notes(user, project: Project, member: ProjectMember) -> "QuerySet[Note]":
    """Apply the note visibility rules for ``user`` on ``project``.

    Rules:
      * PRIVATE      -> only the author sees it.
      * PM_ONLY      -> author + any PROJECT_MANAGER of the project.
      * CONTRIBUTORS -> all project members.
    """
    is_pm = _is_project_manager(user, project)
    base = Note.objects.filter(project=project)

    visibility_q = (
        Q(visibility=NoteVisibility.CONTRIBUTORS)
        | Q(visibility=NoteVisibility.PRIVATE, author=member)
    )
    if is_pm:
        visibility_q |= Q(visibility=NoteVisibility.PM_ONLY)
    else:
        visibility_q |= Q(visibility=NoteVisibility.PM_ONLY, author=member)

    return base.filter(visibility_q).distinct()


def _can_edit(user, project: Project, member: ProjectMember, note: Note) -> bool:
    """A note is editable by its author, and by any PM if visibility is
    PM_ONLY or CONTRIBUTORS."""
    if note.author_id == member.id:
        return True
    if _is_project_manager(user, project) and note.visibility in (
        NoteVisibility.PM_ONLY,
        NoteVisibility.CONTRIBUTORS,
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@login_required
@require_GET
def note_list(request, project_pk: int):
    project = get_object_or_404(Project, pk=project_pk)
    member = _require_project_member(request.user, project)

    show_archived = request.GET.get("archived") == "1"
    qs = _visible_notes(request.user, project, member)
    if not show_archived:
        qs = qs.filter(archived=False)

    return render(
        request,
        "notes/list.html",
        {
            "project": project,
            "notes": qs,
            "show_archived": show_archived,
            "is_project_manager": _is_project_manager(request.user, project),
            "current_member": member,
        },
    )


@login_required
@require_GET
def note_detail(request, project_pk: int, pk: int):
    project = get_object_or_404(Project, pk=project_pk)
    member = _require_project_member(request.user, project)
    qs = _visible_notes(request.user, project, member)
    note = get_object_or_404(qs, pk=pk)
    return render(
        request,
        "notes/detail.html",
        {
            "project": project,
            "note": note,
            "rendered_content": render_markdown(note.content_markdown),
            "can_edit": _can_edit(request.user, project, member, note),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_create(request, project_pk: int):
    project = get_object_or_404(Project, pk=project_pk)
    member = _require_project_member(request.user, project)
    is_pm = _is_project_manager(request.user, project)

    form = NoteForm(request.POST or None, is_project_manager=is_pm)
    if request.method == "POST" and form.is_valid():
        note = form.save(commit=False)
        note.project = project
        note.author = member
        note.save()
        messages.success(request, "Note created.")
        return redirect(
            "notes:detail", project_pk=project.pk, pk=note.pk
        )
    return render(
        request,
        "notes/form.html",
        {"project": project, "form": form, "note": None},
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_update(request, project_pk: int, pk: int):
    project = get_object_or_404(Project, pk=project_pk)
    member = _require_project_member(request.user, project)
    qs = _visible_notes(request.user, project, member)
    note = get_object_or_404(qs, pk=pk)
    if not _can_edit(request.user, project, member, note):
        raise PermissionDenied("You cannot edit this note.")

    is_pm = _is_project_manager(request.user, project)
    form = NoteForm(request.POST or None, instance=note, is_project_manager=is_pm)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Note updated.")
        return redirect("notes:detail", project_pk=project.pk, pk=note.pk)
    return render(
        request,
        "notes/form.html",
        {"project": project, "form": form, "note": note},
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_delete(request, project_pk: int, pk: int):
    project = get_object_or_404(Project, pk=project_pk)
    member = _require_project_member(request.user, project)
    qs = _visible_notes(request.user, project, member)
    note = get_object_or_404(qs, pk=pk)
    if not _can_edit(request.user, project, member, note):
        raise PermissionDenied("You cannot delete this note.")
    if request.method == "POST":
        note.delete()
        messages.success(request, "Note deleted.")
        return HttpResponseRedirect(
            reverse("notes:list", kwargs={"project_pk": project.pk})
        )
    return render(
        request,
        "notes/confirm_delete.html",
        {"project": project, "note": note},
    )
