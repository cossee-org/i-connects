from __future__ import annotations

from apps.core.enums import MemberPrivileges


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    from .models import Invitation, JoinRequest, ProjectPrivilege
    from apps.core.enums import JoinRequestStatus
    from apps.tasks.models import Task

    pending_invitations_count = Invitation.objects.addressed_to(request.user).count()

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

    # Requests sent *to* the user (invitations, incoming join requests) --
    # excludes join requests the user sent out themselves.
    requests_total = pending_invitations_count + incoming_join_requests_count

    # The Tasks page's total (its header and All tab): open tasks assigned
    # to the user or waiting on their review or verification.
    active_tasks_total = Task.objects.my_list_counts(request.user)["total"]

    return {
        "sidebar_pending_invitations": pending_invitations_count,
        "sidebar_incoming_join_requests": incoming_join_requests_count,
        "sidebar_my_join_requests": my_join_requests_count,
        "sidebar_requests_total": requests_total,
        "sidebar_active_tasks_total": active_tasks_total,
    }
