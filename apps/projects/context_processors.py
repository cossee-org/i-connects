from __future__ import annotations

from apps.core.enums import MemberPrivileges


def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}

    from .models import Invitation, JoinRequest, ProjectPrivilege
    from apps.core.enums import JoinRequestStatus

    pm_project_ids = ProjectPrivilege.objects.filter(
        user=request.user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).values_list("project_id", flat=True)

    pending_invitations_count = Invitation.objects.filter(
        project_id__in=pm_project_ids
    ).count()

    incoming_join_requests_count = JoinRequest.objects.filter(
        project_id__in=pm_project_ids,
        status=JoinRequestStatus.PENDING,
    ).count()

    return {
        "sidebar_pending_invitations": pending_invitations_count,
        "sidebar_incoming_join_requests": incoming_join_requests_count,
    }
