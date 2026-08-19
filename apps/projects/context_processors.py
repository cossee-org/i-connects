from __future__ import annotations

from apps.core.enums import MemberPrivileges


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    from django.db.models import OuterRef, Q, Subquery

    from .models import Invitation, JoinRequest, ProjectMember, ProjectPrivilege
    from apps.core.enums import JoinRequestStatus, TaskStatus
    from apps.tasks.models import Task, TaskLog

    user_email = (request.user.email or "").lower()
    pending_invitations_count = Invitation.objects.filter(
        email__iexact=user_email
    ).count()

    pm_project_ids = ProjectPrivilege.objects.filter(
        user=request.user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).values_list("project_id", flat=True)

    incoming_join_requests_count = JoinRequest.objects.filter(
        project_id__in=pm_project_ids,
        status=JoinRequestStatus.PENDING,
    ).count()

    my_join_requests_count = JoinRequest.objects.filter(
        user=request.user,
        status=JoinRequestStatus.PENDING,
    ).count()

    requests_total = (
        pending_invitations_count
        + incoming_join_requests_count
        + my_join_requests_count
    )

    # Active (not-completed) task count -- same definition as the dashboard's
    # active_task_count + review_task_count, and the Tasks page's default,
    # untoggled tab totals.
    user_member_qs = ProjectMember.objects.filter(users=request.user)
    latest_log_status = Subquery(
        TaskLog.objects.filter(task=OuterRef("pk"), assigned_to__in=user_member_qs)
        .order_by("-created_at")
        .values("status")[:1]
    )
    active_assigned_tasks_count = (
        Task.objects.filter(assigned_members__users=request.user, status=TaskStatus.NOT_COMPLETED)
        .annotate(_user_log_status=latest_log_status)
        # NULL-safe: a task with no TaskLog yet has _user_log_status=NULL, and
        # `.exclude(_user_log_status=COMPLETED)` would silently drop it too.
        .filter(Q(_user_log_status__isnull=True) | ~Q(_user_log_status=TaskStatus.COMPLETED))
        .distinct()
        .count()
    )
    active_review_tasks_count = (
        Task.objects.filter(reviewers__users=request.user, status=TaskStatus.NOT_COMPLETED)
        .distinct()
        .count()
    )
    active_tasks_total = active_assigned_tasks_count + active_review_tasks_count

    return {
        "sidebar_pending_invitations": pending_invitations_count,
        "sidebar_incoming_join_requests": incoming_join_requests_count,
        "sidebar_my_join_requests": my_join_requests_count,
        "sidebar_requests_total": requests_total,
        "sidebar_active_tasks_total": active_tasks_total,
    }
