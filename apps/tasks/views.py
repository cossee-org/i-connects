"""Tasks app views: kanban board, task CRUD, milestones, and HTMX endpoints.

All views require authentication; project-scoped views additionally require
project membership (or PROJECT_MANAGER privilege) via ``ProjectContextMixin``
/ ``TaskMemberAccessMixin``.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Max, Min, ProtectedError, Q
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import resolve, reverse, Resolver404
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from urllib.parse import urlparse
from django.views.generic import (
    CreateView,
    DeleteView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from apps.audit.services import AuditAction, log_action
from apps.comments.forms import CommentForm
from apps.core.enums import CompletedAs, TaskStatus
from apps.projects.models import Project, ProjectMember
from apps.tasks.calendar import (
    VIEWS as CALENDAR_VIEWS,
    build_day_tasks,
    build_month_grid,
    build_week_grid,
    build_year_grid,
    parse_view_date,
    shift_date,
)
from apps.tasks.forms import (
    KanbanBoardForm,
    MilestoneForm,
    TaskAssignForm,
    TaskForm,
    TaskMilestoneForm,
)
from apps.tasks.ics import build_ics
from apps.tasks.import_csv import import_rows, parse_csv
from apps.tasks.import_form import TaskImportForm
from apps.tasks.mixins import (
    ProjectContextMixin,
    ProjectManagerRequiredMixin,
    TaskMemberAccessMixin,
)
from apps.tasks.models import ActivityEvent, KanbanBoard, Milestone, Task, TaskActivity, TaskLog
from apps.tasks.services import (
    TaskNotReadyToClose,
    approve_completion,
    close_task,
    complete_task,
    mark_own_done,
    move_task,
    reopen_own,
    reopen_task,
)


# ---------------------------------------------------------------------------
# All tasks (cross-project)
# ---------------------------------------------------------------------------


class MyTaskListView(LoginRequiredMixin, ListView):
    """Every task — done and not done — across every project the user
    belongs to. The one place tasks can be filtered and browsed across
    projects; paginated so a heavy task load never gets fetched at once.
    """

    model = Task
    template_name = "tasks/my_list.html"
    context_object_name = "tasks"
    paginate_by = 20

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["tasks/partials/my_list_results.html"]
        return [self.template_name]

    _SORT_MAP = {
        "newest": "-created_at",
        "oldest": "created_at",
        "deadline_asc": "deadline",
        "deadline_desc": "-deadline",
        "name_asc": "name",
        "name_desc": "-name",
    }

    def get_queryset(self):
        user = self.request.user
        qs = (
            Task.objects.filter(project__privileges__user=user)
            .select_related("project", "container")
            .prefetch_related("assigned_members")
            .distinct()
        )

        status = self.request.GET.get("status", "")
        if status in (TaskStatus.COMPLETED, TaskStatus.NOT_COMPLETED):
            qs = qs.filter(status=status)

        project_id = self.request.GET.get("project", "")
        if project_id:
            qs = qs.filter(project_id=project_id)

        if self.request.GET.get("priority", "") == "high":
            qs = qs.filter(is_priority=True)

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(project__name__icontains=q)
            ).distinct()

        sort = self.request.GET.get("sort", "newest")
        if sort in ("deadline_asc", "deadline_desc"):
            qs = qs.filter(deadline__isnull=False)
        order = self._SORT_MAP.get(sort, "-created_at")
        return qs.order_by(order, "id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        current_q = self.request.GET.get("q", "").strip()
        current_sort = self.request.GET.get("sort", "newest")
        current_status = self.request.GET.get("status", "")
        current_project = self.request.GET.get("project", "")
        current_priority = self.request.GET.get("priority", "")

        context["current_q"] = current_q
        context["current_sort"] = current_sort
        context["current_status"] = current_status
        context["current_project"] = current_project
        context["current_priority"] = current_priority

        context["my_projects"] = (
            Project.objects.filter(privileges__user=user)
            .distinct()
            .order_by("name")
        )

        params = self.request.GET.copy()
        params.pop("page", None)
        context["filter_params"] = params.urlencode()

        context["total_task_count"] = (
            Task.objects.filter(project__privileges__user=user).distinct().count()
        )

        has_filters = bool(
            current_q or current_status or current_project or current_priority
        )
        if has_filters:
            total = context["paginator"].count
            context["results_summary"] = (
                f"{total} result{'s' if total != 1 else ''}"
            )

        return context


# ---------------------------------------------------------------------------
# Kanban board
# ---------------------------------------------------------------------------


def _fmt_short(d) -> str:
    return f"{d.strftime('%b')} {d.day}"


def _calendar_context(request, project) -> dict:
    view = request.GET.get("view", "month")
    if view not in CALENDAR_VIEWS:
        view = "month"
    anchor = parse_view_date(request.GET.get("date"))
    tasks = Task.objects.filter(project=project).filter(
        Q(deadline__isnull=False) | Q(start_date__isnull=False)
    ).prefetch_related("assigned_members")

    context = {
        "calendar_view": view,
        "calendar_date": anchor.isoformat(),
        "calendar_prev_date": shift_date(view, anchor, -1).isoformat(),
        "calendar_next_date": shift_date(view, anchor, 1).isoformat(),
        "calendar_today_date": timezone.localdate().isoformat(),
    }

    if view == "year":
        months = build_year_grid(anchor.year, tasks)
        context["calendar_year_months"] = months
        context["calendar_label"] = str(anchor.year)
    elif view == "week":
        week = build_week_grid(anchor, tasks)
        context["calendar_week"] = week
        start, end = week["days"][0].date, week["days"][-1].date
        if start.month == end.month:
            context["calendar_label"] = f"{_fmt_short(start)}–{end.day}, {end.year}"
        else:
            context["calendar_label"] = f"{_fmt_short(start)} – {_fmt_short(end)}, {end.year}"
    elif view == "day":
        day_tasks = build_day_tasks(anchor, tasks)
        context["calendar_day_tasks"] = day_tasks
        context["calendar_day_date"] = anchor
        context["calendar_label"] = f"{anchor.strftime('%A, %B')} {anchor.day}, {anchor.year}"
    else:
        weeks = build_month_grid(anchor.year, anchor.month, tasks)
        context["calendar_weeks"] = weeks
        context["calendar_label"] = anchor.strftime("%B %Y")

    return context


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
        context.update(_calendar_context(self.request, self.project))
        return context


class BoardListPartialView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """HTMX partial that renders the list view section with fresh data."""

    template_name = "tasks/task_list_view.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["columns"] = (
            KanbanBoard.objects.filter(project=self.project)
            .prefetch_related("tasks__assigned_members")
            .order_by("container_order")
        )
        return context


class BoardKanbanPartialView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """HTMX partial that renders the kanban board section with fresh data."""

    template_name = "tasks/task_board_partial.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["columns"] = (
            KanbanBoard.objects.filter(project=self.project)
            .prefetch_related("tasks__assigned_members")
            .order_by("container_order")
        )
        return context


class BoardCalendarPartialView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """HTMX partial that renders the calendar view section for a given month.

    Only tasks with ``deadline`` and/or ``start_date`` set are included —
    tasks missing both calendar fields never reach the template.
    """

    template_name = "tasks/task_calendar_view.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_calendar_context(self.request, self.project))
        return context


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


class TaskCreateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, CreateView
):
    template_name = "tasks/task_form.html"
    form_class = TaskForm
    model = Task

    def get_template_names(self):
        # HTMX requests (kanban modal) get the bare partial; direct page
        # visits get the full base-layout wrapper.
        if self.request.headers.get("HX-Request"):
            return ["tasks/task_form.html"]
        return ["tasks/task_form_page.html"]

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        container_id = self.request.GET.get("container")
        if container_id:
            initial["container"] = container_id
        else:
            first = KanbanBoard.objects.filter(project=self.project).order_by("container_order").first()
            if first:
                initial["container"] = first.pk

        # A date range picked on the calendar view (?start_date=&deadline=,
        # both YYYY-MM-DD) pre-fills the new task's span; the user still
        # fills in the rest and saves — nothing is created automatically.
        start_date_param = parse_date(self.request.GET.get("start_date") or "")
        if start_date_param:
            initial["start_date"] = timezone.make_aware(
                datetime.combine(start_date_param, time.min)
            )
        deadline_param = parse_date(self.request.GET.get("deadline") or "")
        if deadline_param:
            initial["deadline"] = timezone.make_aware(
                datetime.combine(deadline_param, time(23, 59))
            )
        return initial

    def form_valid(self, form):
        task: Task = form.save(commit=False)
        task.project = self.project
        task.created_by = self.project_member or _fallback_member(
            self.request.user, self.project
        )
        # container_task_order — place at the top of the column so a newly
        # created task surfaces first (Task ordering is ascending).
        min_order = Task.objects.filter(container=task.container).aggregate(
            m=Min("container_task_order")
        )["m"]
        task.container_task_order = 0 if min_order is None else min_order - 1
        task.save()
        form.save_m2m()
        # Seed a TaskLog per assignee (mirrors the Blitz createTask behaviour).
        _seed_assignee_logs(task)
        log_action(
            AuditAction.TASK_CREATED,
            actor=self.request.user,
            entity=task,
            project=task.project,
            metadata={"name": task.name},
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
        next_url = self.request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return redirect(next_url)
        return redirect(self._detail_url(task))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_action"] = reverse(
            "tasks:create", kwargs={"project_pk": self.project.pk}
        )
        context["form_title"] = "Create a new task"
        next_url = self.request.GET.get("next") or self.request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            context["next_url"] = next_url
        return context

    def _detail_url(self, task: Task) -> str:
        return reverse(
            "tasks:detail",
            kwargs={"project_pk": self.project.pk, "pk": task.pk},
        )


class TaskDetailPaneView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    """HTMX side-pane partial — board peek view, no base layout."""

    template_name = "tasks/task_detail_pane.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = get_object_or_404(
            Task.objects.select_related("container", "milestone", "created_by")
            .prefetch_related("assigned_members", "reviewers", "task_logs", "comments__author"),
            project=self.project,
            pk=kwargs["pk"],
        )
        context["task"] = task
        context["comment_form"] = CommentForm()
        context["status_block"] = _task_status_context(
            task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )
        from_pane = "board"
        referer = self.request.META.get("HTTP_REFERER", "")
        if referer:
            parsed = urlparse(referer)
            try:
                match = resolve(parsed.path)
                view_name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
                if view_name == "projects:detail" and match.kwargs.get("pk") == self.project.pk:
                    from_pane = "project"
            except Resolver404:
                pass
        detail_url = reverse("tasks:detail", kwargs={"project_pk": self.project.pk, "pk": kwargs["pk"]})
        context["full_page_url"] = f"{detail_url}?from_pane={from_pane}"
        return context


class TaskDetailView(LoginRequiredMixin, ProjectContextMixin, TemplateView):
    template_name = "tasks/task_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = get_object_or_404(
            Task.objects.select_related("container", "milestone", "created_by")
            .prefetch_related("assigned_members", "reviewers", "task_logs__comments__author", "comments__author", "activities__actor", "activities__member")
            .filter(project=self.project),
            pk=kwargs["pk"],
        )
        context["task"] = task
        context["task_activities"] = task.activities.select_related("actor", "member")
        context["comment_form"] = CommentForm()
        context["assign_form"] = TaskAssignForm(
            project=self.project,
            initial={"assigned_members": task.assigned_members.all()},
        )
        context["milestone_form"] = TaskMilestoneForm(
            project=self.project, initial={"milestone": task.milestone}
        )
        context["status_block"] = _task_status_context(
            task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )
        origin = self._resolve_origin()
        context["back_url"] = origin["url"]
        context["back_label"] = origin["label"]
        context["delete_redirect_url"] = origin["url"]
        return context

    def _resolve_origin(self) -> dict:
        """Identify the page the task was opened from.

        Returns ``{"url": ..., "label": ...}`` for either the originating
        Project view or Kanban board. Falls back to the Kanban board when
        the Referer is missing, off-host, or points elsewhere.

        When ``?from_pane=board`` or ``?from_pane=project`` is present the
        task was opened from the side pane, so append ``?open_task=<pk>`` to
        the return URL so the originating page can re-open the pane.
        """
        task_pk = self.kwargs["pk"]
        board_url = reverse("tasks:board", kwargs={"project_pk": self.project.pk})
        project_url = reverse("projects:detail", kwargs={"pk": self.project.pk})

        from_pane = self.request.GET.get("from_pane")
        if from_pane == "board":
            return {"url": f"{board_url}?open_task={task_pk}", "label": "Tasks"}
        if from_pane == "project":
            return {"url": f"{project_url}?open_task={task_pk}", "label": "Project"}

        board = {"url": board_url, "label": "Tasks"}
        project = {"url": project_url, "label": "Project"}
        referer = self.request.META.get("HTTP_REFERER", "")
        if not referer:
            return board
        parsed = urlparse(referer)
        if parsed.netloc and parsed.netloc != self.request.get_host():
            return board
        try:
            match = resolve(parsed.path)
        except Resolver404:
            return board
        view_name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
        if view_name == "projects:detail" and match.kwargs.get("pk") == self.project.pk:
            return project
        return board


class TaskUpdateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, UpdateView
):
    template_name = "tasks/task_form.html"
    form_class = TaskForm
    model = Task

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["tasks/task_form.html"]
        return ["tasks/task_form_page.html"]

    def get_queryset(self):
        return Task.objects.filter(project=self.project)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def form_valid(self, form):
        task = form.save()
        # Seed Activity & comments entries for any newly-assigned members,
        # mirroring the Assignees ("Change assignees") flow.
        _seed_assignee_logs(task)
        log_action(
            AuditAction.TASK_UPDATED,
            actor=self.request.user,
            entity=task,
            project=task.project,
            metadata={"name": task.name},
        )
        if self.request.headers.get("HX-Request"):
            response = render(
                self.request,
                "tasks/partials/task_summary.html",
                {"task": task, "project": self.project},
            )
            # ``assignees-changed`` refreshes the task detail timeline if the
            # edit happened from there; ``task-updated`` refreshes board/project.
            response["HX-Trigger"] = json.dumps(
                {"task-updated": {"id": task.id}, "assignees-changed": True}
            )
            return response
        messages.success(self.request, f"Task '{task.name}' updated.")
        next_url = self.request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return redirect(next_url)
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
        next_url = self.request.GET.get("next") or self.request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            context["next_url"] = next_url
        return context


class TaskImportView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, FormView
):
    """Bulk-create tasks from a CSV upload. Project Lead only."""

    template_name = "tasks/task_import.html"
    form_class = TaskImportForm

    def form_valid(self, form):
        upload = form.cleaned_data["csv_file"]
        wrapped = io.TextIOWrapper(upload, encoding="utf-8", newline="")
        rows, parse_errors = parse_csv(wrapped)

        if parse_errors:
            for err in parse_errors:
                form.add_error(None, err)
            return self.form_invalid(form)

        created, row_errors = import_rows(
            self.project, rows, user=self.request.user
        )

        if created == 0 and row_errors and row_errors[0].startswith("import failed"):
            # A hard rollback — surface it as a form error and re-render.
            for err in row_errors:
                form.add_error(None, err)
            return self.form_invalid(form)

        messages.success(self.request, f"{created} tasks imported.")
        for warning in row_errors:
            messages.warning(self.request, warning)
        return redirect("tasks:board", project_pk=self.project.pk)


class TaskDeleteView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, DeleteView
):
    template_name = "tasks/task_confirm_delete.html"
    model = Task

    def get_queryset(self):
        return Task.objects.filter(project=self.project)

    def get_success_url(self):
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return reverse("tasks:board", kwargs={"project_pk": self.project.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_contributions"] = _task_has_contributions(self.object)
        return context

    def form_valid(self, form):
        task = self.get_object()
        # Closed tasks generate append-only ContributionRecord rows whose FKs
        # PROTECT the task and its logs. A hard delete would raise
        # ProtectedError (a 500); refuse up front with a clear message.
        if _task_has_contributions(task):
            return self._blocked_response(
                "This task has recorded contributions and can't be deleted."
            )
        task_pk = task.pk
        task_name = task.name
        project = task.project
        actor = self.request.user
        try:
            response = super().form_valid(form)
        except ProtectedError:
            # Defensive: catch any other protected relation we don't pre-check.
            return self._blocked_response(
                "This task is referenced by protected records and can't be deleted."
            )
        log_action(
            AuditAction.TASK_DELETED,
            actor=actor,
            entity_type="task",
            entity_id=task_pk,
            project=project,
            metadata={"name": task_name},
        )
        if self.request.headers.get("HX-Request"):
            return HttpResponse(status=204)
        return response

    def _blocked_response(self, message: str):
        if self.request.headers.get("HX-Request"):
            return HttpResponse(message, status=409)
        messages.error(self.request, message)
        return redirect(
            "tasks:detail", project_pk=self.project.pk, pk=self.object.pk
        )


# ---------------------------------------------------------------------------
# HTMX task actions: move, complete, reopen, assign, approve
# ---------------------------------------------------------------------------


class TaskMoveView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
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


class TaskChangeColumnView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
    """Keyboard-accessible alternative to SortableJS drag-drop.

    Accepts a form POST with ``container_id`` and appends the task to the
    end of the target column. This closes the a11y gap flagged during the
    WCAG pass: users who can't use a mouse can reach the task detail page
    (which is just a link) and move the task via the same endpoint the
    drag-drop uses, without needing pointer coordination.
    """

    def post(self, request, *args, **kwargs):
        try:
            container_id = int(request.POST.get("container_id", ""))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("container_id is required")

        # Place at the end of the destination column.
        try:
            dest = KanbanBoard.objects.get(
                pk=container_id, project=self.task.project
            )
        except KanbanBoard.DoesNotExist:
            raise Http404("Destination column not found for this project.")

        end_of_col = (
            Task.objects.filter(container=dest).aggregate(m=Max("container_task_order"))["m"] or -1
        ) + 1
        move_task(self.task, container_id=dest.pk, order=end_of_col)
        messages.success(request, f"Moved to '{dest.name}'.")
        return redirect(
            "tasks:detail", project_pk=self.task.project_id, pk=self.task.pk
        )


def _task_status_context(task, *, project_member, is_project_manager):
    """Context for the completion status block (per-assignee done state)."""
    current = {}
    for log in task.task_logs.all():  # newest-first; first seen is current
        current.setdefault(log.assigned_to_id, log)
    assignees = list(task.assigned_members.all())
    assignee_states = [
        {
            "member": m,
            "done": (
                (log := current.get(m.id)) is not None
                and log.status == TaskStatus.COMPLETED
            ),
        }
        for m in assignees
    ]
    my_state = next(
        (s for s in assignee_states if project_member and s["member"].id == project_member.id),
        None,
    )
    return {
        "task": task,
        "project": task.project,
        "is_project_manager": is_project_manager,
        "is_team_mode": task.completion_mode == CompletedAs.TEAM,
        "assignee_states": assignee_states,
        "done_count": sum(1 for s in assignee_states if s["done"]),
        "assignee_count": len(assignee_states),
        "all_done": task.all_assignees_done,
        "is_assignee": my_state is not None,
        "my_done": bool(my_state and my_state["done"]),
    }


def _respond_status_block(request, task, *, project_member, is_project_manager):
    """Render the status block for HTMX, or redirect to the task for full GETs."""
    if request.headers.get("HX-Request"):
        pane_mode = request.POST.get("pane_mode") == "1"
        ctx = _task_status_context(
            task, project_member=project_member, is_project_manager=is_project_manager
        )
        ctx["pane_mode"] = pane_mode
        return render(request, "tasks/partials/task_status_block.html", ctx)
    return redirect("tasks:detail", project_pk=task.project_id, pk=task.pk)


class TaskStatusBlockView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """GET endpoint that re-renders the status block partial (e.g. after edits)."""

    def get(self, request, *args, **kwargs):
        pane_mode = request.GET.get("pane") == "1"
        ctx = _task_status_context(
            self.task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )
        ctx["pane_mode"] = pane_mode
        return render(request, "tasks/partials/task_status_block.html", ctx)


class TaskCompleteView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """An assignee marks *their own* part of an INDIVIDUAL-mode task done."""

    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        if self.task.completion_mode == CompletedAs.TEAM:
            return HttpResponseBadRequest(
                "This task is completed as a team; a Lead closes it."
            )
        if not self.task.assigned_members.filter(pk=self.project_member.pk).exists():
            return HttpResponseForbidden("Only an assignee can mark their part done.")
        mark_own_done(task=self.task, member=self.project_member)
        return _respond_status_block(
            request, self.task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )


class TaskUndoOwnView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """An assignee undoes their own INDIVIDUAL-mode completion."""

    def post(self, request, *args, **kwargs):
        if self.project_member is None:
            return HttpResponseBadRequest("No project membership.")
        if not self.task.assigned_members.filter(pk=self.project_member.pk).exists():
            return HttpResponseForbidden("Only an assignee can undo their mark.")
        reopen_own(task=self.task, member=self.project_member)
        return _respond_status_block(
            request, self.task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )


class TaskCloseView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
    """A Project Lead closes the task (finalises Task.status = COMPLETED)."""

    def post(self, request, *args, **kwargs):
        closer = self.project_member or _fallback_member(request.user, self.project)
        try:
            close_task(task=self.task, closed_by=closer)
        except TaskNotReadyToClose as exc:
            return HttpResponseBadRequest(str(exc))
        return _respond_status_block(
            request, self.task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )


class TaskReopenView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
    """A Project Lead reopens a closed task, clearing all completion state."""

    def post(self, request, *args, **kwargs):
        reopener = self.project_member or _fallback_member(request.user, self.project)
        reopen_task(task=self.task, completed_by=reopener)
        return _respond_status_block(
            request, self.task,
            project_member=self.project_member,
            is_project_manager=self.is_project_manager,
        )


def _task_activity_context(task, *, is_project_manager):
    """Context for rendering the Activity timeline partial."""
    return {
        "task": task,
        "task_activities": task.activities.select_related("actor", "member"),
        "is_project_manager": is_project_manager,
    }


def _seed_assignee_logs(task, actor=None) -> bool:
    """Seed a TaskLog (Activity & comments entry) for each assigned member
    that doesn't have one yet.

    Idempotent — keying off existing logs means it can run on create, on
    edit, and on assignee changes without producing duplicates. Returns
    ``True`` when at least one log was created.
    """
    logged_member_ids = set(task.task_logs.values_list("assigned_to_id", flat=True))
    new_members = [
        member
        for member in task.assigned_members.all()
        if member.pk not in logged_member_ids
    ]
    if new_members:
        TaskLog.objects.bulk_create([
            TaskLog(
                task=task,
                assigned_to=member,
                completed_as=(CompletedAs.TEAM if member.name else CompletedAs.INDIVIDUAL),
            )
            for member in new_members
        ])
    return bool(new_members)


class TaskAssignView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
    def post(self, request, *args, **kwargs):
        form = TaskAssignForm(request.POST, project=self.project)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid assignment data.")
        members = form.cleaned_data.get("assigned_members") or []
        self.task.assigned_members.set(members)
        # Ensure every assigned member has an Activity & comments entry. Keying
        # off existing logs makes this idempotent and also backfills members
        # assigned without a log.
        _seed_assignee_logs(self.task, actor=self.project_member)
        # Fallback for when htmx isn't running (e.g. the script failed to
        # load): a native form submit reloads the task page, which renders
        # the full timeline server-side — so seeded threads still appear.
        if not request.headers.get("HX-Request"):
            return redirect(
                "tasks:detail", project_pk=self.project.pk, pk=self.task.pk
            )
        response = render(
            request,
            "tasks/partials/task_assignees.html",
            {"task": self.task, "project": self.project},
        )
        # Tell the page to refresh the Activity & comments timeline so any
        # newly-seeded entries appear without a full reload.
        response["HX-Trigger"] = "assignees-changed"
        return response

    def get(self, request, *args, **kwargs):
        # Direct (non-HTMX) navigation lands here; send users to the task.
        return redirect(
            "tasks:detail", project_pk=self.project.pk, pk=self.task.pk
        )


class TaskActivityView(LoginRequiredMixin, TaskMemberAccessMixin, View):
    """Render the Activity & comments timeline (used for HTMX refreshes)."""

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "tasks/partials/task_log_list.html",
            _task_activity_context(
                self.task, is_project_manager=self.is_project_manager
            ),
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
        approve_completion(task_log=log, approved=approved, verifier_user=request.user)
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

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["tasks/partials/board_columns_modal.html"]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["columns"] = KanbanBoard.objects.filter(project=self.project)
        context["form"] = KanbanBoardForm()
        return context


class BoardColumnCreateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
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
            {"column": column, "project": self.project, "is_project_manager": True},
        )


class BoardColumnUpdateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
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
            {"column": column, "project": self.project, "is_project_manager": True},
        )


class BoardColumnReorderView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    """Persist a new ordering for this project's kanban columns."""

    def post(self, request, *args, **kwargs):
        try:
            ids = [int(x) for x in request.POST.getlist("order")]
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid order payload.")
        existing_ids = set(
            KanbanBoard.objects.filter(project=self.project).values_list(
                "id", flat=True
            )
        )
        if set(ids) != existing_ids or len(ids) != len(existing_ids):
            return HttpResponseBadRequest(
                "Order must include every column exactly once."
            )
        with transaction.atomic():
            for idx, col_id in enumerate(ids):
                KanbanBoard.objects.filter(pk=col_id, project=self.project).update(
                    container_order=idx
                )
        return HttpResponse(status=204)


class BoardColumnDeleteView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    def post(self, request, *args, **kwargs):
        column = get_object_or_404(
            KanbanBoard, pk=kwargs["pk"], project=self.project
        )
        if column.tasks.exists():
            return HttpResponseBadRequest(
                "Cannot delete a column that still contains tasks. "
                "Move or delete its tasks first."
            )
        column.delete()
        # Empty 200 (not 204) so htmx swaps and the row is removed.
        return HttpResponse("")


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


class MilestoneCreateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
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
            {"milestone": milestone, "project": self.project, "is_project_manager": True},
        )


class MilestoneUpdateView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
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
            {"milestone": milestone, "project": self.project, "is_project_manager": True},
        )


class MilestoneDeleteView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    def post(self, request, *args, **kwargs):
        milestone = get_object_or_404(
            Milestone, pk=kwargs["pk"], project=self.project
        )
        milestone.delete()
        return HttpResponse(status=204)


class MilestoneCalendarView(LoginRequiredMixin, ProjectContextMixin, View):
    """Serve project milestones as an ICS (iCalendar) feed.

    Researchers can subscribe to this URL from Google Calendar / Apple
    Calendar / Outlook to see milestone deadlines alongside their own
    events. Access is gated by project membership (via ``ProjectContextMixin``).
    """

    def get(self, request, *args, **kwargs):
        milestones = Milestone.objects.filter(project=self.project)
        ics_text = build_ics(
            milestones,
            calendar_name=f"{self.project.name} — milestones",
        )
        response = HttpResponse(
            ics_text, content_type="text/calendar; charset=utf-8"
        )
        filename = f"milestones-{slugify(self.project.name) or self.project.pk}.ics"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class TaskMilestoneAssignView(
    LoginRequiredMixin, ProjectManagerRequiredMixin, TaskMemberAccessMixin, View
):
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


def _task_has_contributions(task) -> bool:
    """True when the task has append-only ContributionRecord rows.

    Such records PROTECT the task (and its logs), so a hard delete would
    raise ProtectedError. Imported lazily to avoid a hard dependency on the
    contributions app at module load.
    """
    from apps.contributions.models import ContributionRecord

    return ContributionRecord.objects.filter(task=task).exists()


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
