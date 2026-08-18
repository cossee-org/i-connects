"""Server-rendered views for the projects app (Phase 3B)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET

from apps.audit.services import AuditAction, log_action
from apps.core.enums import MemberPrivileges


_SORT_LABELS = {
    "newest": None,
    "oldest": "oldest first",
    "name_asc": "name A–Z",
    "name_desc": "name Z–A",
}

_STATUS_LABELS = {
    "PLANNING": "planning",
    "ACTIVE": "active",
    "PAUSED": "paused",
    "COMPLETED": "completed",
    "INACTIVE": "inactive",
}

def _build_results_summary(count, q, status, sort, visibility=None):
    """Return a human-readable results summary string, or None if no filters active."""
    parts = []
    if visibility:
        parts.append(visibility)
    if status:
        parts.append(_STATUS_LABELS.get(status, status.lower()))
    if parts:
        desc = " ".join(parts) + " projects"
    else:
        desc = "projects"
    if q:
        desc += f' matching “{q}”'
    sort_label = _SORT_LABELS.get(sort)
    has_filters = bool(q or status or visibility or sort_label)
    if not has_filters:
        return None, count
    n = f"{count} result{'s' if count != 1 else ''}"
    summary = f"{n} for {desc}"
    if sort_label:
        summary += f", sorted by {sort_label}"
    return summary, count

from apps.core.enums import JoinRequestStatus

from .forms import (
    InvitationForm,
    JoinRequestDeclineForm,
    JoinRequestForm,
    MemberPrivilegeForm,
    ProjectForm,
)
from .mixins import (
    ProjectContextMixin,
    ProjectManagerRequiredMixin,
    ProjectMemberRequiredMixin,
)
from .models import (
    Invitation,
    JoinRequest,
    Project,
    ProjectMember,
    ProjectPrivilege,
)
from .services import (
    JoinRequestError,
    LastProjectManagerError,
    accept_invitation,
    approve_join_request,
    assert_pm_self_action_allowed,
    cancel_join_request,
    create_invitation,
    create_join_request,
    create_project_with_owner,
    decline_invitation,
    decline_join_request,
    is_last_project_manager,
)


# ---------------------------------------------------------------------------
# Project list / detail / CRUD
# ---------------------------------------------------------------------------


class DashboardView(LoginRequiredMixin, TemplateView):
    """Personal dashboard — active tasks, overdue, pending approvals, stats."""

    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        from django.db.models import Count, OuterRef, Subquery, Q
        from django.utils import timezone
        from apps.core.enums import TaskStatus, MemberPrivileges
        from apps.tasks.models import Task, TaskLog
        from apps.notifications.models import Notification

        now = timezone.now()
        soon = now + timezone.timedelta(days=7)

        # --- Privilege map -------------------------------------------------
        privs = ProjectPrivilege.objects.filter(user=user).select_related("project")
        privs_map = {p.project_id: p.privilege for p in privs}
        pm_project_ids = [
            pid for pid, priv in privs_map.items()
            if priv == MemberPrivileges.PROJECT_MANAGER
        ]

        # --- Projects ------------------------------------------------------
        user_projects = (
            Project.objects.filter(privileges__user=user)
            .distinct()
            .order_by("-updated_at")
        )
        context["project_count"] = user_projects.count()
        context["recent_projects"] = list(user_projects)

        # --- Task querysets ------------------------------------------------
        user_member_qs = ProjectMember.objects.filter(users=user)

        latest_log_status = Subquery(
            TaskLog.objects.filter(
                task=OuterRef("pk"),
                assigned_to__in=user_member_qs,
            )
            .order_by("-created_at")
            .values("status")[:1]
        )

        assigned_tasks = (
            Task.objects.filter(
                assigned_members__users=user,
                status=TaskStatus.NOT_COMPLETED,
            )
            .annotate(_user_log_status=latest_log_status)
            # NULL-safe: a task with no TaskLog yet has _user_log_status=NULL,
            # and `exclude(_user_log_status=COMPLETED)` would silently drop it
            # too (NOT (NULL = COMPLETED) is NULL/false in SQL), so it must be
            # explicitly kept.
            .filter(Q(_user_log_status__isnull=True) | ~Q(_user_log_status=TaskStatus.COMPLETED))
            .select_related("project", "container")
            .order_by("deadline", "id")
            .distinct()
        )

        all_active = list(assigned_tasks)
        overdue = [t for t in all_active if t.deadline and t.deadline < now]
        due_soon = [t for t in all_active if t.deadline and now <= t.deadline <= soon]
        no_deadline = [t for t in all_active if not t.deadline]

        context["overdue_tasks"] = overdue
        context["due_soon_tasks"] = due_soon
        context["no_deadline_tasks"] = no_deadline
        context["all_open_tasks"] = all_active
        context["active_task_count"] = len(all_active)
        context["overdue_count"] = len(overdue)
        context["now"] = now

        # --- Pending approvals (PM only) -----------------------------------
        if pm_project_ids:
            pending_logs = list(
                TaskLog.objects.filter(
                    status=TaskStatus.COMPLETED,
                    approved__isnull=True,
                    task__project_id__in=pm_project_ids,
                )
                .select_related("task", "task__project", "assigned_to")
                .order_by("-created_at")
            )
        else:
            pending_logs = []
        context["pending_logs"] = pending_logs
        context["pending_count"] = len(pending_logs)
        context["is_pm_anywhere"] = bool(pm_project_ids)

        # --- Pending join requests (PM only) ----------------------------------
        if pm_project_ids:
            pending_join_requests = list(
                JoinRequest.objects.filter(
                    project_id__in=pm_project_ids,
                    status=JoinRequestStatus.PENDING,
                )
                .select_related("user", "project")
                .order_by("created_at")
            )
        else:
            pending_join_requests = []
        context["pending_join_requests"] = pending_join_requests
        context["pending_join_request_count"] = len(pending_join_requests)

        # --- Invitation requests (invitations addressed to the current user) ---
        invitation_requests = list(
            Invitation.objects.filter(email__iexact=(user.email or "").lower())
            .select_related("project")
            .order_by("-created_at")
        )
        context["invitation_requests"] = invitation_requests
        context["invitation_request_count"] = len(invitation_requests)

        # --- Notifications -------------------------------------------------
        context["recent_notifications"] = list(
            Notification.objects.unread_for_user(user)
            .select_related("project")
            .order_by("-created_at")[:8]
        )
        context["unread_notification_count"] = (
            Notification.objects.unread_for_user(user).count()
        )

        return context


class PublicProjectListView(ListView):
    """Grid of public projects -- accessible to anonymous visitors."""

    model = Project
    template_name = "projects/list.html"
    context_object_name = "projects"
    paginate_by = 12

    _SORT_MAP = {
        "newest": "-created_at",
        "oldest": "created_at",
        "name_asc": "name",
        "name_desc": "-name",
    }

    def get_queryset(self):
        from django.db.models import Q
        qs = Project.objects.filter(is_public=True)
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(abstract__icontains=q))
        sort = self.request.GET.get("sort", "newest")
        order = self._SORT_MAP.get(sort, "-created_at")
        return qs.order_by(order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_q"] = self.request.GET.get("q", "")
        context["current_sort"] = self.request.GET.get("sort", "newest")
        context["current_status"] = self.request.GET.get("status", "")
        from apps.core.enums import ProjectStatus
        _STATUS_ORDER = ["PLANNING", "ACTIVE", "PAUSED", "COMPLETED", "INACTIVE"]
        context["project_statuses"] = [
            c for key in _STATUS_ORDER for c in ProjectStatus.choices if c[0] == key
        ]
        params = self.request.GET.copy()
        params.pop("page", None)
        context["filter_params"] = params.urlencode()

        total = context["paginator"].count
        context["results_summary"], _ = _build_results_summary(
            total,
            context["current_q"],
            context["current_status"],
            context["current_sort"],
        )

        # Build project_rows with member privileges for member name display
        pm = MemberPrivileges.PROJECT_MANAGER.value
        project_ids = [p.id for p in context["projects"]]
        all_member_privs = (
            ProjectPrivilege.objects
            .filter(project_id__in=project_ids)
            .select_related("user")
        )
        members_by_project: dict[int, list] = {}
        for mp in all_member_privs:
            members_by_project.setdefault(mp.project_id, []).append(mp)
        for pid in members_by_project:
            members_by_project[pid].sort(key=lambda p: (0 if p.privilege == pm else 1))

        context["project_rows"] = [
            {"project": p, "members": members_by_project.get(p.id, [])}
            for p in context["projects"]
        ]
        context["total_project_count"] = Project.objects.filter(is_public=True).count()
        return context


class MyProjectListView(LoginRequiredMixin, ListView):
    """Projects the current user is a member of."""

    model = Project
    template_name = "projects/my_list.html"
    context_object_name = "projects"
    paginate_by = 12

    _SORT_MAP = {
        "newest": "-created_at",
        "oldest": "created_at",
        "name_asc": "name",
        "name_desc": "-name",
    }
    _VALID_PER_PAGE = {6, 12, 24, 48}

    def get_paginate_by(self, queryset):
        try:
            per_page = int(self.request.GET.get("per_page", self.paginate_by))
        except (TypeError, ValueError):
            per_page = self.paginate_by
        return per_page if per_page in self._VALID_PER_PAGE else self.paginate_by

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        qs = Project.objects.filter(privileges__user=user).distinct()
        status = self.request.GET.get("status", "")
        if status:
            qs = qs.filter(status=status)
        visibility = self.request.GET.get("visibility", "")
        if visibility == "public":
            qs = qs.filter(is_public=True)
        elif visibility == "private":
            qs = qs.filter(is_public=False)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(abstract__icontains=q)
                | Q(privileges__user__given_name__icontains=q)
                | Q(privileges__user__family_name__icontains=q)
                | Q(privileges__user__email__icontains=q)
            ).distinct()
        sort = self.request.GET.get("sort", "newest")
        order = self._SORT_MAP.get(sort, "-created_at")
        return qs.order_by(order)

    def get_context_data(self, **kwargs):
        from django.db.models import Count
        from django.utils import timezone

        from apps.core.enums import TaskStatus
        from apps.tasks.models import Task

        context = super().get_context_data(**kwargs)
        user = self.request.user
        privs_map = {
            p.project_id: p.privilege
            for p in ProjectPrivilege.objects.filter(user=user)
        }

        # Fetch all member privileges for visible projects in one query.
        project_ids = [p.id for p in context["projects"]]
        all_member_privs = (
            ProjectPrivilege.objects
            .filter(project_id__in=project_ids)
            .select_related("user")
            # Leads (PROJECT_MANAGER) first, then contributors
            .order_by("project_id", "privilege")
        )
        members_by_project: dict[int, list] = {}
        for mp in all_member_privs:
            members_by_project.setdefault(mp.project_id, []).append(mp)
        # Re-sort so PROJECT_MANAGER always comes before CONTRIBUTOR
        pm = MemberPrivileges.PROJECT_MANAGER.value
        for pid in members_by_project:
            members_by_project[pid].sort(
                key=lambda p: (0 if p.privilege == pm else 1)
            )

        # Open/overdue task counts and pending join-request counts, one
        # aggregate query each so this stays flat regardless of page size.
        open_tasks_by_project = {
            row["project_id"]: row["n"]
            for row in (
                Task.objects
                .filter(project_id__in=project_ids)
                .exclude(status=TaskStatus.COMPLETED)
                .values("project_id")
                .annotate(n=Count("id"))
            )
        }
        now = timezone.now()
        due_tasks_by_project = {
            row["project_id"]: row["n"]
            for row in (
                Task.objects
                .filter(project_id__in=project_ids, deadline__lt=now)
                .exclude(status=TaskStatus.COMPLETED)
                .values("project_id")
                .annotate(n=Count("id"))
            )
        }
        pending_requests_by_project = {
            row["project_id"]: row["n"]
            for row in (
                JoinRequest.objects
                .filter(project_id__in=project_ids, status=JoinRequestStatus.PENDING)
                .values("project_id")
                .annotate(n=Count("id"))
            )
        }

        # Templates can't do dict lookups by variable key, so pre-zip here.
        context["project_rows"] = [
            {
                "project": p,
                "privilege": privs_map.get(p.id),
                "members": members_by_project.get(p.id, []),
                "open_task_count": open_tasks_by_project.get(p.id, 0),
                "due_task_count": due_tasks_by_project.get(p.id, 0),
                "pending_join_request_count": (
                    pending_requests_by_project.get(p.id, 0)
                    if privs_map.get(p.id) == pm
                    else 0
                ),
            }
            for p in context["projects"]
        ]

        # Filter / sort context
        context["current_q"] = self.request.GET.get("q", "")
        context["current_sort"] = self.request.GET.get("sort", "newest")
        context["current_status"] = self.request.GET.get("status", "")
        context["current_visibility"] = self.request.GET.get("visibility", "")
        context["current_per_page"] = self.get_paginate_by(self.get_queryset())
        context["valid_per_page"] = sorted(self._VALID_PER_PAGE)
        from apps.core.enums import ProjectStatus
        _STATUS_ORDER = ["PLANNING", "ACTIVE", "PAUSED", "COMPLETED", "INACTIVE"]
        context["project_statuses"] = [
            c for key in _STATUS_ORDER for c in ProjectStatus.choices if c[0] == key
        ]
        context["create_url"] = reverse("projects:create")
        params = self.request.GET.copy()
        params.pop("page", None)
        context["filter_params"] = params.urlencode()

        total = context["paginator"].count
        context["total_project_count"] = (
            Project.objects.filter(privileges__user=user).distinct().count()
        )
        context["results_summary"], _ = _build_results_summary(
            total,
            context["current_q"],
            context["current_status"],
            context["current_sort"],
            context["current_visibility"],
        )

        return context


class ProjectDetailView(DetailView):
    """Project overview page.

    Public projects are visible to anyone. Private projects require the
    viewer to be a member.
    """

    model = Project
    template_name = "projects/detail.html"
    context_object_name = "project"

    def get_object(self, queryset=None):
        project = super().get_object(queryset)
        if not project.is_public:
            if not self.request.user.is_authenticated:
                raise PermissionDenied("This project is private.")
            is_member = ProjectPrivilege.objects.filter(
                user=self.request.user, project=project
            ).exists()
            if not is_member:
                raise PermissionDenied("This project is private.")
        return project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        all_members = list(
            ProjectMember.objects.filter(project=project, deleted=False)
            .prefetch_related("users", "roles")
            .order_by("id")
        )
        lead_user_ids = set(
            ProjectPrivilege.objects.filter(
                project=project,
                privilege=MemberPrivileges.PROJECT_MANAGER,
            ).values_list("user_id", flat=True)
        )
        leads, regulars = [], []
        for m in all_members:
            if any(u.id in lead_user_ids for u in m.users.all()):
                leads.append(m)
            else:
                regulars.append(m)
        context["members"] = all_members
        context["project_leads"] = leads
        context["regular_members"] = regulars
        if self.request.user.is_authenticated:
            user_priv = (
                ProjectPrivilege.objects.filter(
                    user=self.request.user, project=project
                )
                .order_by("-created_at")
                .first()
            )
            context["user_privilege"] = user_priv.privilege if user_priv else None
            context["pending_join_request"] = (
                JoinRequest.objects.filter(
                    project=project,
                    user=self.request.user,
                    status=JoinRequestStatus.PENDING,
                ).first()
            )
            user_email = (self.request.user.email or "").lower()
            context["pending_invitation"] = (
                Invitation.objects.filter(
                    project=project,
                    email__iexact=user_email,
                ).first()
            )
            if context["user_privilege"] == MemberPrivileges.PROJECT_MANAGER:
                pending_jrs = list(
                    JoinRequest.objects.filter(
                        project=project, status=JoinRequestStatus.PENDING
                    ).select_related("user").order_by("-created_at")[:20]
                )
                context["pending_join_requests"] = pending_jrs
                context["pending_join_request_count"] = JoinRequest.objects.filter(
                    project=project, status=JoinRequestStatus.PENDING
                ).count()
                context["pending_invitation_count"] = Invitation.objects.filter(
                    project=project
                ).count()
            else:
                context["pending_join_requests"] = []
                context["pending_join_request_count"] = 0
                context["pending_invitation_count"] = 0
        else:
            context["user_privilege"] = None
            context["pending_join_request"] = None
            context["pending_invitation"] = None

        context["is_project_manager"] = (
            context["user_privilege"] == MemberPrivileges.PROJECT_MANAGER
        )
        # Drives the project section tabs in the top bar (members only).
        context["is_project_member"] = context["user_privilege"] is not None

        # Task summary — only meaningful for members, but the queries are
        # cheap enough to run for any viewer who can see the project.
        from apps.core.enums import TaskStatus
        from apps.tasks.models import KanbanBoard, Task
        from django.utils import timezone

        context["today"] = timezone.now()
        task_qs = Task.objects.filter(project=project)
        context["task_count"] = task_qs.count()
        context["overdue_count"] = task_qs.filter(
            deadline__lt=context["today"],
        ).count()
        context["contributor_count"] = ProjectPrivilege.objects.filter(
            project=project
        ).values("user").distinct().count()
        context["tasks"] = (
            task_qs.select_related("container")
            .order_by("-updated_at")[:20]
        )
        context["has_kanban_columns"] = KanbanBoard.objects.filter(
            project=project
        ).exists()
        return context


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = "projects/form.html"

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["projects/partials/create_form.html"]
        return ["projects/form.html"]

    def form_valid(self, form):
        project = create_project_with_owner(
            user=self.request.user, form_data=form.cleaned_data
        )
        if self.request.headers.get("HX-Request"):
            import json
            response = HttpResponse(status=204)
            response["HX-Trigger"] = json.dumps(
                {"project-created": {"url": reverse("projects:detail", kwargs={"pk": project.pk})}}
            )
            return response
        messages.success(self.request, f"Project '{project.name}' created.")
        return HttpResponseRedirect(
            reverse("projects:detail", kwargs={"pk": project.pk})
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "New project"
        context["submit_label"] = "Create project"
        return context


class ProjectUpdateView(ProjectManagerRequiredMixin, ProjectContextMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = "projects/form.html"

    def get_success_url(self):
        return reverse("projects:detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(
            AuditAction.PROJECT_UPDATED,
            actor=self.request.user,
            entity=self.object,
            project=self.object,
            metadata={"name": self.object.name},
        )
        messages.success(self.request, "Project updated.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Edit project"
        context["submit_label"] = "Save changes"
        return context


class ProjectDeleteView(ProjectManagerRequiredMixin, DeleteView):
    model = Project
    template_name = "projects/confirm_delete.html"
    success_url = reverse_lazy("projects:my_list")

    def form_valid(self, form):
        project = self.object
        project_name = project.name
        log_action(
            AuditAction.PROJECT_DELETED,
            actor=self.request.user,
            entity=project,
            project=project,
            metadata={"name": project_name},
        )
        messages.success(self.request, f"Project '{project_name}' deleted.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def _member_rows(project, *, viewer=None):
    """Collect (member, user, privilege, privilege_form) dicts for a project.

    When ``viewer`` is supplied, each row is annotated with ``is_self`` so
    the template can disable PM-mutating controls on the viewer's own row
    when the last-PM invariant would otherwise be broken.
    """
    members = (
        ProjectMember.objects.filter(project=project, deleted=False)
        .prefetch_related("users", "roles")
        .order_by("id")
    )
    privileges = {
        p.user_id: p.privilege
        for p in ProjectPrivilege.objects.filter(project=project)
    }
    viewer_id = getattr(viewer, "id", None) if viewer is not None else None
    rows = []
    for m in members:
        primary_user = m.users.first()
        effective = (
            privileges.get(primary_user.id)
            if primary_user
            else MemberPrivileges.CONTRIBUTOR
        )
        rows.append(
            {
                "member": m,
                "user": primary_user,
                "privilege": effective,
                "is_self": (
                    primary_user is not None and primary_user.id == viewer_id
                ),
                "form": MemberPrivilegeForm(
                    initial={"privilege": effective or MemberPrivileges.CONTRIBUTOR}
                ),
            }
        )
    return rows


class ProjectMemberListView(
    ProjectMemberRequiredMixin, ProjectContextMixin, TemplateView
):
    template_name = "projects/members.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rows"] = _member_rows(self.project, viewer=self.request.user)
        context["viewer_is_last_pm"] = is_last_project_manager(
            project=self.project, user=self.request.user
        )
        if context["user_privilege"] == MemberPrivileges.PROJECT_MANAGER:
            context["pending_join_request_count"] = JoinRequest.objects.filter(
                project=self.project, status=JoinRequestStatus.PENDING
            ).count()
            context["pending_invitation_count"] = Invitation.objects.filter(
                project=self.project
            ).count()
        return context


class ContributionTrackingView(
    ProjectMemberRequiredMixin, ProjectContextMixin, TemplateView
):
    template_name = "projects/contribution_tracking.html"


class WikiView(
    ProjectMemberRequiredMixin, ProjectContextMixin, TemplateView
):
    template_name = "projects/wiki.html"


class ProjectFormsView(
    ProjectMemberRequiredMixin, ProjectContextMixin, TemplateView
):
    template_name = "projects/forms.html"


class ProjectInvitationListView(
    ProjectManagerRequiredMixin, ProjectContextMixin, ListView
):
    """PM-facing list of pending (unsent/unanswered) invitations for a project."""

    template_name = "projects/invitation_requests.html"
    context_object_name = "invitations"

    def get_queryset(self):
        return (
            Invitation.objects.filter(project=self.project)
            .select_related("invited_user")
            .order_by("-created_at")
        )


class CancelInvitationView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, invitation_pk):
        invitation = get_object_or_404(Invitation, pk=invitation_pk, project=self.project)
        invitation.delete()
        messages.success(request, "Invitation cancelled.")
        return redirect("projects:project_invitations", pk=self.project.pk)


class UpdateMemberPrivilegeView(
    ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    """HTMX POST target -- updates the privilege of a member's primary user."""

    http_method_names = ["post"]

    def post(self, request, pk, member_pk):
        member = get_object_or_404(ProjectMember, pk=member_pk, project=self.project)
        form = MemberPrivilegeForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest("Invalid privilege value.")

        new_privilege = form.cleaned_data["privilege"]
        primary_user = member.users.first()
        if primary_user is None:
            return HttpResponseBadRequest("Member has no linked user.")

        try:
            assert_pm_self_action_allowed(
                project=self.project,
                actor=request.user,
                target_user=primary_user,
                new_privilege=new_privilege,
            )
        except LastProjectManagerError as exc:
            messages.error(request, str(exc))
            if request.headers.get("HX-Request"):
                return HttpResponse(str(exc), status=400)
            return HttpResponseRedirect(
                reverse("projects:members", kwargs={"pk": self.project.pk})
            )

        ProjectPrivilege.objects.filter(
            user=primary_user, project=self.project
        ).delete()
        ProjectPrivilege.objects.create(
            user=primary_user, project=self.project, privilege=new_privilege
        )
        log_action(
            AuditAction.MEMBER_PRIVILEGE_CHANGED,
            actor=request.user,
            project=self.project,
            metadata={
                "target_user_id": primary_user.id,
                "to": new_privilege,
            },
        )

        row = {
            "member": member,
            "user": primary_user,
            "privilege": new_privilege,
            "is_self": primary_user.id == request.user.id,
            "form": MemberPrivilegeForm(initial={"privilege": new_privilege}),
        }
        return render(
            request,
            "projects/member_row.html",
            {
                "row": row,
                "project": self.project,
                "user_privilege": MemberPrivileges.PROJECT_MANAGER,
                "viewer_is_last_pm": is_last_project_manager(
                    project=self.project, user=request.user
                ),
            },
        )


class RemoveMemberView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    http_method_names = ["post", "delete"]

    def post(self, request, pk, member_pk):
        return self._remove(request, member_pk)

    def delete(self, request, pk, member_pk):
        return self._remove(request, member_pk)

    def _remove(self, request, member_pk):
        member = get_object_or_404(ProjectMember, pk=member_pk, project=self.project)
        primary_user = member.users.first()

        if primary_user is not None:
            try:
                assert_pm_self_action_allowed(
                    project=self.project,
                    actor=request.user,
                    target_user=primary_user,
                    new_privilege=None,
                )
            except LastProjectManagerError as exc:
                messages.error(request, str(exc))
                if request.headers.get("HX-Request"):
                    return HttpResponse(str(exc), status=400)
                return HttpResponseRedirect(
                    reverse("projects:members", kwargs={"pk": self.project.pk})
                )

        member.deleted = True
        member.save(update_fields=["deleted", "updated_at"])
        if primary_user is not None:
            ProjectPrivilege.objects.filter(
                user=primary_user, project=self.project
            ).delete()
        log_action(
            AuditAction.MEMBER_REMOVED,
            actor=request.user,
            project=self.project,
            metadata={
                "target_user_id": primary_user.id if primary_user else None
            },
        )
        if request.headers.get("HX-Request"):
            # Empty response removes the row via hx-swap="outerHTML".
            return HttpResponse("")
        return HttpResponseRedirect(
            reverse("projects:members", kwargs={"pk": self.project.pk})
        )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@login_required
@require_GET
def user_invite_search(request, pk):
    """Return an HTML fragment of users matching ``?q=`` for the invite modal search."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    project = get_object_or_404(Project, pk=pk)
    if not ProjectPrivilege.objects.filter(
        project=project,
        user=request.user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists():
        return HttpResponse(status=403)

    q = request.GET.get("q", "").strip()
    if not q:
        return render(
            request,
            "projects/partials/user_search_results.html",
            {"users_data": [], "q": q},
        )

    User = get_user_model()
    existing_member_ids = list(
        ProjectPrivilege.objects.filter(project=project).values_list("user_id", flat=True)
    )
    already_invited_ids = list(
        Invitation.objects.filter(project=project, invited_user__isnull=False)
        .values_list("invited_user_id", flat=True)
    )
    qs = (
        User.objects.exclude(pk__in=existing_member_ids)
        .exclude(pk__in=already_invited_ids)
        .filter(
            Q(name__icontains=q)
            | Q(given_name__icontains=q)
            | Q(family_name__icontains=q)
            | Q(email__icontains=q)
        )
        .order_by("name", "email")[:10]
    )

    users_data = []
    for u in qs:
        if u.given_name or u.family_name:
            full_name = f"{u.given_name} {u.family_name}".strip()
        else:
            full_name = u.name
        label = f"{full_name} ({u.email})" if full_name else u.email
        users_data.append({"user": u, "label": label})

    return render(
        request,
        "projects/partials/user_search_results.html",
        {"users_data": users_data, "q": q},
    )


class InviteCreateView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    """GET renders the invite modal fragment; POST sends the invitation."""

    template_name = "projects/invite_form.html"

    def get(self, request, pk):
        form = InvitationForm(project=self.project)
        return render(
            request,
            self.template_name,
            {"form": form, "project": self.project},
        )

    def post(self, request, pk):
        form = InvitationForm(request.POST, project=self.project)
        if not form.is_valid():
            selected_user_label = ""
            user_pk = request.POST.get("invited_user")
            if user_pk:
                from django.contrib.auth import get_user_model
                try:
                    u = get_user_model().objects.get(pk=user_pk)
                    if u.given_name or u.family_name:
                        full_name = f"{u.given_name} {u.family_name}".strip()
                    else:
                        full_name = u.name
                    selected_user_label = f"{full_name} ({u.email})" if full_name else u.email
                except get_user_model().DoesNotExist:
                    pass
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "project": self.project,
                    "selected_user_label": selected_user_label,
                },
            )
        added_by = (
            request.user.get_full_name() or request.user.name or request.user.email
        )
        create_invitation(
            project=self.project,
            email=form.cleaned_data["email"],
            privilege=form.cleaned_data["privilege"],
            added_by=added_by,
            roles=form.cleaned_data.get("roles"),
            inviter=request.user,
            invited_user=form.cleaned_data.get("invited_user"),
        )
        recipient_label = (
            getattr(form.cleaned_data.get("invited_user"), "email", None)
            or form.cleaned_data["email"]
        )
        messages.success(request, f"Invitation sent to {recipient_label}.")
        if request.headers.get("HX-Request"):
            response = HttpResponse()
            response["HX-Redirect"] = reverse(
                "projects:members", kwargs={"pk": self.project.pk}
            )
            return response
        return HttpResponseRedirect(
            reverse("projects:members", kwargs={"pk": self.project.pk})
        )


class MyInvitationsView(LoginRequiredMixin, ListView):
    """Pending invitations addressed to the current user's email."""

    model = Invitation
    template_name = "invites/list.html"
    context_object_name = "invitations"

    def get_queryset(self):
        email = (self.request.user.email or "").lower()
        return (
            Invitation.objects.filter(email__iexact=email)
            .select_related("project")
            .order_by("-created_at")
        )


class AcceptOrDeclineInviteView(View):
    """Single URL that handles showing, accepting, and declining an invite."""

    template_name = "invites/accept.html"

    def get(self, request, code):
        invitation = get_object_or_404(Invitation, invitation_code=code)
        if not request.user.is_authenticated:
            login_url = reverse("account_login")
            return HttpResponseRedirect(f"{login_url}?next={request.path}")
        return render(
            request,
            self.template_name,
            {"invitation": invitation, "project": invitation.project},
        )

    def post(self, request, code):
        if not request.user.is_authenticated:
            login_url = reverse("account_login")
            return HttpResponseRedirect(f"{login_url}?next={request.path}")
        invitation = get_object_or_404(Invitation, invitation_code=code)
        action = request.POST.get("action", "accept")

        # Only the invited email may act on the invitation.
        user_email = (request.user.email or "").lower()
        if invitation.email.lower() != user_email:
            raise PermissionDenied(
                "This invitation is addressed to a different account."
            )

        if action == "decline":
            decline_invitation(invitation=invitation, user=request.user)
            messages.info(request, "Invitation declined.")
            return redirect("projects:my_invitations")

        project = accept_invitation(invitation=invitation, user=request.user)
        messages.success(request, f"Joined '{project.name}'.")
        return redirect("projects:detail", pk=project.pk)


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------


def _normalize_requested_privilege(raw: str | None) -> str:
    if raw == MemberPrivileges.PROJECT_MANAGER:
        return MemberPrivileges.PROJECT_MANAGER
    return MemberPrivileges.CONTRIBUTOR


class RequestToJoinView(LoginRequiredMixin, View):
    """Show the join-request form (GET) and submit it (POST).

    Accessible to any logged-in non-member. For private projects, the user
    must have somehow reached the URL — they cannot view detail without
    membership, so this endpoint can only be hit if they already had the
    link or the project is public.
    """

    template_name = "projects/join_request_form.html"

    def _get_project(self, pk: int) -> Project:
        project = get_object_or_404(Project, pk=pk)
        if not project.is_public and not ProjectPrivilege.objects.filter(
            user=self.request.user, project=project
        ).exists():
            # Private projects: only members can interact at all. The
            # detail view would already block this, but be explicit.
            raise PermissionDenied("This project is private.")
        return project

    def _already_member(self, project: Project) -> bool:
        return ProjectPrivilege.objects.filter(
            user=self.request.user, project=project
        ).exists()

    _modal_template = "projects/partials/join_request_form_modal.html"

    def get(self, request, pk):
        project = self._get_project(pk)
        if self._already_member(project):
            messages.info(request, "You are already a member of this project.")
            return redirect("projects:detail", pk=project.pk)

        pending = JoinRequest.objects.filter(
            project=project,
            user=request.user,
            status=JoinRequestStatus.PENDING,
        ).first()
        if pending is not None:
            messages.info(request, "You already have a pending request for this project.")
            return redirect("projects:detail", pk=project.pk)

        initial_privilege = _normalize_requested_privilege(
            request.GET.get("privilege")
        )
        form = JoinRequestForm(initial_privilege=initial_privilege)
        template = self._modal_template if request.headers.get("HX-Request") else self.template_name
        return render(request, template, {"form": form, "project": project})

    def post(self, request, pk):
        project = self._get_project(pk)
        is_htmx = bool(request.headers.get("HX-Request"))
        form = JoinRequestForm(request.POST)
        if not form.is_valid():
            template = self._modal_template if is_htmx else self.template_name
            return render(request, template, {"form": form, "project": project})

        try:
            create_join_request(
                project=project,
                user=request.user,
                requested_privilege=form.cleaned_data["requested_privilege"],
                message=form.cleaned_data.get("message", ""),
            )
        except JoinRequestError as exc:
            messages.error(request, str(exc))
            redirect_url = reverse("projects:detail", pk=project.pk)
            if is_htmx:
                response = HttpResponse(status=204)
                response["HX-Redirect"] = redirect_url
                return response
            return redirect(redirect_url)

        messages.success(
            request,
            f"Your request to join '{project.name}' was sent to the project leads.",
        )

        if is_htmx:
            pending = JoinRequest.objects.filter(
                project=project,
                user=request.user,
                status=JoinRequestStatus.PENDING,
            ).first()
            response = render(
                request,
                "projects/partials/join_action.html",
                {"pending_join_request": pending, "project": project},
            )
            response["HX-Retarget"] = "#join-action"
            response["HX-Reswap"] = "innerHTML"
            response["HX-Trigger"] = "closejoinmodal"
            return response

        return redirect("projects:detail", pk=project.pk)


class ReviewJoinRequestView(ProjectManagerRequiredMixin, ProjectContextMixin, View):
    """GET — show approve/decline review page for a single join request."""

    template_name = "projects/review_join_request.html"

    def get(self, request, pk, join_request_pk):
        join_request = get_object_or_404(
            JoinRequest,
            pk=join_request_pk,
            project=self.project,
            status=JoinRequestStatus.PENDING,
        )
        template = (
            "projects/partials/review_join_request_modal.html"
            if request.headers.get("HX-Request")
            else self.template_name
        )
        return render(
            request,
            template,
            {
                "join_request": join_request,
                "project": self.project,
                "source": request.GET.get("source", ""),
            },
        )


class ProjectJoinRequestListView(
    ProjectManagerRequiredMixin, ProjectContextMixin, ListView
):
    """PM-facing list of pending join requests for a project."""

    template_name = "projects/join_requests.html"
    context_object_name = "join_requests"

    def get_queryset(self):
        return (
            JoinRequest.objects.filter(
                project=self.project, status=JoinRequestStatus.PENDING
            )
            .select_related("user")
            .order_by("created_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["decline_form"] = JoinRequestDeclineForm()
        return context


class ApproveJoinRequestView(
    ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    http_method_names = ["post"]

    def post(self, request, pk, join_request_pk):
        join_request = get_object_or_404(
            JoinRequest, pk=join_request_pk, project=self.project
        )
        try:
            approve_join_request(join_request=join_request, approver=request.user)
        except JoinRequestError as exc:
            messages.error(request, str(exc))
            return redirect("projects:join_requests", pk=self.project.pk)

        messages.success(request, "Join request approved.")
        if request.headers.get("HX-Request"):
            return HttpResponse("")
        if request.POST.get("next") == "incoming":
            return redirect("projects:incoming_join_requests")
        return redirect("projects:join_requests", pk=self.project.pk)


class DeclineJoinRequestView(
    ProjectManagerRequiredMixin, ProjectContextMixin, View
):
    http_method_names = ["post"]

    def post(self, request, pk, join_request_pk):
        join_request = get_object_or_404(
            JoinRequest, pk=join_request_pk, project=self.project
        )
        form = JoinRequestDeclineForm(request.POST)
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        try:
            decline_join_request(
                join_request=join_request,
                approver=request.user,
                reason=reason,
            )
        except JoinRequestError as exc:
            messages.error(request, str(exc))
            return redirect("projects:join_requests", pk=self.project.pk)

        messages.info(request, "Join request declined.")
        if request.headers.get("HX-Request"):
            return HttpResponse("")
        if request.POST.get("next") == "incoming":
            return redirect("projects:incoming_join_requests")
        return redirect("projects:join_requests", pk=self.project.pk)


class CancelJoinRequestView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, join_request_pk):
        join_request = get_object_or_404(
            JoinRequest, pk=join_request_pk, user=request.user
        )
        project = join_request.project
        is_htmx = bool(request.headers.get("HX-Request"))

        try:
            cancel_join_request(join_request=join_request, user=request.user)
        except JoinRequestError as exc:
            messages.error(request, str(exc))
            if is_htmx:
                pending = JoinRequest.objects.filter(
                    project=project,
                    user=request.user,
                    status=JoinRequestStatus.PENDING,
                ).first()
                return render(
                    request,
                    "projects/partials/join_action.html",
                    {"pending_join_request": pending, "project": project},
                )
            return redirect("projects:detail", pk=project.pk)

        if is_htmx:
            return render(
                request,
                "projects/partials/join_action.html",
                {"pending_join_request": None, "project": project},
            )

        messages.info(request, "Join request cancelled.")
        return redirect("projects:detail", pk=project.pk)


class MyJoinRequestsView(LoginRequiredMixin, ListView):
    template_name = "projects/my_join_requests.html"
    context_object_name = "join_requests"

    def get_queryset(self):
        return (
            JoinRequest.objects.filter(user=self.request.user)
            .select_related("project")
            .order_by("-created_at")
        )


class IncomingJoinRequestsView(LoginRequiredMixin, ListView):
    template_name = "projects/incoming_join_requests.html"
    context_object_name = "join_requests"

    def get_queryset(self):
        from apps.core.enums import MemberPrivileges

        pm_project_ids = ProjectPrivilege.objects.filter(
            user=self.request.user,
            privilege=MemberPrivileges.PROJECT_MANAGER,
        ).values_list("project_id", flat=True)

        return (
            JoinRequest.objects.filter(
                project_id__in=pm_project_ids,
                status=JoinRequestStatus.PENDING,
            )
            .select_related("user", "project")
            .order_by("created_at")
        )


@login_required
@require_GET
def my_projects_badge(request):
    count = Project.objects.filter(privileges__user=request.user).distinct().count()
    return HttpResponse(str(count) if count else "")


