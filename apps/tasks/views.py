"""Tasks app views: kanban board, task CRUD, milestones, and HTMX endpoints.

All views require authentication; project-scoped views additionally require
project membership (or PROJECT_MANAGER privilege) via ``ProjectContextMixin``
/ ``TaskMemberAccessMixin``.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.comments.forms import CommentForm
from apps.core.enums import CompletedAs
from apps.projects.models import ProjectMember
from apps.tasks.forms import (
    KanbanBoardForm,
    MilestoneForm,
    TaskAssignForm,
    TaskForm,
    TaskMilestoneForm,
)
from apps.tasks.mixins import ProjectContextMixin, TaskMemberAccessMixin
from apps.tasks.models import KanbanBoard, Milestone, Task, TaskLog
from apps.tasks.services import (
    approve_completion,
    complete_task,
    move_task,
    reopen_task,
)


# ---------------------------------------------------------------------------
# Kanban board
# ---------------------------------------------------------------------------


class BoardView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """Main kanban board for a project."""

    template_name = "tasks/board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        columns = (
            KanbanBoard.objects.filter(project=self.project)
            .prefetch_related("tasks__assigned_members")
            .order_by("container_order")
        )
        context["columns"] = columns
        return context


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


class TaskCreateView(LoginRequiredMixin, ProjectContextMixin, CreateView):
    template_name = "tasks/task_form.html"
    form_class = TaskForm
    model = Task

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        container_id = self.request.GET.get("container")
        if container_id:
            initial["container"] = container_id
        return initial

    def form_valid(self, form):
        task: Task = form.save(commit=False)
        task.project = self.project
        task.created_by = self.project_member or _fallback_member(
            self.request.user, self.project
        )
        # container_task_order — append to end of column
        max_order = Task.objects.filter(container=task.container).aggregate(
            m=Max("container_task_order")
        )["m"]
        task.container_task_order = 0 if max_order is None else max_order + 1
        task.save()
        form.save_m2m()
        # Seed a TaskLog per assignee (mirrors the Blitz createTask behaviour).
        for member in task.assigned_members.all():
            TaskLog.objects.create(
                task=task,
                assigned_to=member,
                completed_as=(
                    CompletedAs.TEAM if member.name else CompletedAs.INDIVIDUAL
                ),
            )
        if self.request.htmx if hasattr(self.request, "htmx") else self.request.headers.get(
            "HX-Request"
        ):
            # Swap the new card onto the board.
            response = render(
                self.request,
                "tasks/task_card.html",
                {"task": task, "project": self.project},
            )
            response["HX-Trigger"] = json.dumps({"task-created": {"id": task.id}})
            return response
        messages.success(self.request, f"Task '{task.name}' created.")
        return redirect(self._detail_url(task))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_action"] = reverse(
            "tasks:create", kwargs={"project_pk": self.project.pk}
        )
        context["form_title"] = "New task"
        return context

    def _detail_url(self, task: Task) -> str:
        return reverse(
            "tasks:detail",
            kwargs={"project_pk": self.project.pk, "pk": task.pk},
        )


class TaskDetailView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    template_name = "tasks/task_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = get_object_or_404(
            Task.objects.select_related("container", "milestone", "created_by")
            .prefetch_related("assigned_members", "task_logs__comments__author")
            .filter(project=self.project),
            pk=kwargs["pk"],
        )
        context["task"] = task
        context["task_logs"] = task.task_logs.select_related(
            "assigned_to", "completed_by"
        ).prefetch_related("comments__author")
        context["comment_form"] = CommentForm()
        context["assign_form"] = TaskAssignForm(
            project=self.project,
            initial={"assigned_members": task.assigned_members.all()},
        )
        context["milestone_form"] = TaskMilestoneForm(
            project=self.project, initial={"milestone": task.milestone}
        )
        return context


class TaskUpdateView(LoginRequiredMixin, ProjectContextMixin, UpdateView):
    template_name = "tasks/task_form.html"
    form_class = TaskForm
    model = Task

    def get_queryset(self):
        return Task.objects.filter(project=self.project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        task = form.save()
        if self.request.headers.get("HX-Request"):
            return render(
                self.request,
                "tasks/partials/task_summary.html",
                {"task": task, "project": self.project},
            )
        messages.success(self.request, f"Task '{task.name}' updated.")
        return redirect(
            "tasks:detail",
            project_pk=self.project.pk,
            pk=task.pk,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_action"] = reverse(
            "tasks:update",
            kwargs={"project_pk": self.project.pk, "pk": self.object.pk},
        )
        context["form_title"] = "Edit task"
        return context


class TaskDeleteView(LoginRequiredMixin, ProjectContextMixin, DeleteView):
    template_name = "tasks/task_confirm_delete.html"
    model = Task

    def get_queryset(self):
        return Task.objects.filter(project=self.project)

    def get_success_url(self):
        return reverse("tasks:board", kwargs={"project_pk": self.project.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.headers.get("HX-Request"):
            return HttpResponse(status=204)
        return response


# ---------------------------------------------------------------------------
# HTMX task actions: move, complete, reopen, assign, approve
# ---------------------------------------------------------------------------


class TaskMoveView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """HTMX endpoint: drag-drop task into a new column / position."""

    def post(self, request, *args, **kwargs):
        try:
            container_id = int(request.POST.get("container_id", ""))
            order = int(request.POST.get("order", "0"))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("container_id and order are required")
        try:
            move_task(self.task, container_id=container_id, order=order)
        except KanbanBoard.DoesNotExist:
            raise Http404("Destination column not found for this project.")
        return HttpResponse(status=204)


class TaskCompleteView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        completed_as = request.POST.get("completed_as") or CompletedAs.INDIVIDUAL
        complete_task(
            task=self.task,
            completed_by=self.project_member,
            completed_as=completed_as,
        )
        return _respond_task_card(request, self.task)


class TaskReopenView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        completed_as = request.POST.get("completed_as") or CompletedAs.INDIVIDUAL
        reopen_task(
            task=self.task,
            completed_by=self.project_member,
            completed_as=completed_as,
        )
        return _respond_task_card(request, self.task)


class TaskAssignView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    def post(self, request, *args, **kwargs):
        form = TaskAssignForm(request.POST, project=self.project)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid assignment data.")
        members = form.cleaned_data.get("assigned_members") or []
        self.task.assigned_members.set(members)
        return render(
            request,
            "tasks/partials/task_assignees.html",
            {"task": self.task, "project": self.project},
        )


class TaskLogApproveView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """Project-manager only: approve / reject a TaskLog completion entry."""

    def post(self, request, *args, **kwargs):
        log = get_object_or_404(
            TaskLog.objects.filter(task=self.task), pk=kwargs["log_pk"]
        )
        if not self.is_project_manager:
            return HttpResponse(status=403)
        raw = request.POST.get("approved")
        if raw in {"true", "1", "yes"}:
            approved: bool | None = True
        elif raw in {"false", "0", "no"}:
            approved = False
        else:
            approved = None
        approve_completion(task_log=log, approved=approved)
        return render(
            request,
            "tasks/task_log_item.html",
            {
                "log": log,
                "task": self.task,
                "project": self.project,
                "is_project_manager": self.is_project_manager,
                "comment_form": CommentForm(),
            },
        )


# ---------------------------------------------------------------------------
# Kanban column management
# ---------------------------------------------------------------------------


class BoardColumnListView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    template_name = "tasks/board_columns.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["columns"] = KanbanBoard.objects.filter(project=self.project)
        context["form"] = KanbanBoardForm()
        return context


class BoardColumnCreateView(LoginRequiredMixin, ProjectContextMixin, View):
    def post(self, request, *args, **kwargs):
        form = KanbanBoardForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid column data.")
        max_order = KanbanBoard.objects.filter(project=self.project).aggregate(
            m=Max("container_order")
        )["m"]
        column: KanbanBoard = form.save(commit=False)
        column.project = self.project
        column.container_order = 0 if max_order is None else max_order + 1
        column.save()
        return render(
            request,
            "tasks/partials/kanban_column_header.html",
            {"column": column, "project": self.project},
        )


class BoardColumnUpdateView(LoginRequiredMixin, ProjectContextMixin, View):
    def get(self, request, *args, **kwargs):
        column = get_object_or_404(
            KanbanBoard, pk=kwargs["pk"], project=self.project
        )
        form = KanbanBoardForm(instance=column)
        return render(
            request,
            "tasks/partials/kanban_column_edit.html",
            {"column": column, "form": form, "project": self.project},
        )

    def post(self, request, *args, **kwargs):
        column = get_object_or_404(
            KanbanBoard, pk=kwargs["pk"], project=self.project
        )
        form = KanbanBoardForm(request.POST, instance=column)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid column data.")
        form.save()
        return render(
            request,
            "tasks/partials/kanban_column_header.html",
            {"column": column, "project": self.project},
        )


class BoardColumnDeleteView(LoginRequiredMixin, ProjectContextMixin, View):
    def post(self, request, *args, **kwargs):
        column = get_object_or_404(
            KanbanBoard, pk=kwargs["pk"], project=self.project
        )
        column.delete()
        return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


class MilestoneListView(LoginRequiredMixin, ProjectContextMixin, ListView):
    template_name = "tasks/milestone_list.html"
    context_object_name = "milestones"

    def get_queryset(self):
        return Milestone.objects.filter(project=self.project).prefetch_related("tasks")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = MilestoneForm()
        return context


class MilestoneCreateView(LoginRequiredMixin, ProjectContextMixin, View):
    def post(self, request, *args, **kwargs):
        form = MilestoneForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "tasks/milestone_form.html",
                {"form": form, "project": self.project, "form_title": "New milestone"},
                status=400,
            )
        milestone: Milestone = form.save(commit=False)
        milestone.project = self.project
        milestone.save()
        return render(
            request,
            "tasks/partials/milestone_item.html",
            {"milestone": milestone, "project": self.project},
        )


class MilestoneUpdateView(LoginRequiredMixin, ProjectContextMixin, View):
    def get(self, request, *args, **kwargs):
        milestone = get_object_or_404(
            Milestone, pk=kwargs["pk"], project=self.project
        )
        form = MilestoneForm(instance=milestone)
        return render(
            request,
            "tasks/milestone_form.html",
            {
                "form": form,
                "project": self.project,
                "milestone": milestone,
                "form_title": f"Edit milestone: {milestone.name}",
                "form_action": reverse(
                    "tasks:milestone_update",
                    kwargs={"project_pk": self.project.pk, "pk": milestone.pk},
                ),
            },
        )

    def post(self, request, *args, **kwargs):
        milestone = get_object_or_404(
            Milestone, pk=kwargs["pk"], project=self.project
        )
        form = MilestoneForm(request.POST, instance=milestone)
        if not form.is_valid():
            return render(
                request,
                "tasks/milestone_form.html",
                {
                    "form": form,
                    "project": self.project,
                    "milestone": milestone,
                    "form_title": f"Edit milestone: {milestone.name}",
                },
                status=400,
            )
        form.save()
        return render(
            request,
            "tasks/partials/milestone_item.html",
            {"milestone": milestone, "project": self.project},
        )


class MilestoneDeleteView(LoginRequiredMixin, ProjectContextMixin, View):
    def post(self, request, *args, **kwargs):
        milestone = get_object_or_404(
            Milestone, pk=kwargs["pk"], project=self.project
        )
        milestone.delete()
        return HttpResponse(status=204)


class TaskMilestoneAssignView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """HTMX endpoint: set / clear ``Task.milestone``."""

    def post(self, request, *args, **kwargs):
        form = TaskMilestoneForm(request.POST, project=self.project)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid milestone selection.")
        self.task.milestone = form.cleaned_data.get("milestone")
        self.task.save(update_fields=["milestone", "updated_at"])
        return render(
            request,
            "tasks/partials/task_milestone.html",
            {"task": self.task, "project": self.project},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _respond_task_card(request, task: Task) -> HttpResponse:
    """Return the task card partial after an HTMX mutation, or redirect."""
    if request.headers.get("HX-Request"):
        return render(
            request,
            "tasks/task_card.html",
            {"task": task, "project": task.project},
        )
    return redirect("tasks:detail", project_pk=task.project_id, pk=task.pk)


def _fallback_member(user, project):
    """Return or create a ProjectMember for PMs who don't have an explicit row.

    Only used when a project manager without a membership row creates a task
    (rare, but makes the FK happy). Prefer to rely on the resolved
    ``project_member`` from ``ProjectContextMixin`` when it exists.
    """
    member = (
        ProjectMember.objects.filter(project=project, users=user, deleted=False)
        .order_by("id")
        .first()
    )
    if member is not None:
        return member
    member = ProjectMember.objects.create(project=project, name=None)
    member.users.add(user)
    return member
