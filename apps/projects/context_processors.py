from __future__ import annotations

from apps.core.enums import MemberPrivileges


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    from .models import Invitation, JoinRequest, ProjectPrivilege
    from apps.core.enums import JoinRequestStatus

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

    return {
        "sidebar_pending_invitations": pending_invitations_count,
        "sidebar_incoming_join_requests": incoming_join_requests_count,
        "sidebar_my_join_requests": my_join_requests_count,
        "sidebar_requests_total": requests_total,
    }
