"""Helper services for the projects app.

Small, procedural functions -- deliberately not a service layer. Keeps the
view code easy to read.
"""

from __future__ import annotations

import secrets

from django.db import transaction
from django.utils import timezone

from apps.audit.services import AuditAction, log_action
from apps.core.enums import JoinRequestStatus, MemberPrivileges

from .emails import (
    send_invitation_email,
    send_join_request_decision_email,
    send_join_request_email_to_pms,
)
from .models import (
    Invitation,
    JoinRequest,
    Project,
    ProjectMember,
    ProjectPrivilege,
)


def generate_invitation_code() -> str:
    """Return a URL-safe random code."""
    return secrets.token_urlsafe(24)


@transaction.atomic
def create_project_with_owner(*, user, form_data: dict) -> Project:
    """Create a Project plus a ProjectMember + PM privilege for ``user``."""
    project = Project.objects.create(**form_data)
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project,
        user=user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    )
    log_action(
        AuditAction.PROJECT_CREATED,
        actor=user,
        entity=project,
        project=project,
        metadata={"name": project.name},
    )
    return project


@transaction.atomic
def create_invitation(
    *,
    project: Project,
    email: str | None = None,
    privilege: str,
    added_by: str,
    roles=None,
    inviter=None,
    invited_user=None,
) -> Invitation:
    """Create an invitation and fire the email.

    Exactly one of ``email`` / ``invited_user`` must be supplied. When
    ``invited_user`` is given, the invitation's email is taken from that
    user's account so the existing accept/decline flow keeps working.
    """
    if invited_user is not None:
        resolved_email = (invited_user.email or "").lower()
    else:
        if not email:
            raise ValueError("create_invitation requires email or invited_user")
        resolved_email = email.lower()

    invitation = Invitation.objects.create(
        project=project,
        email=resolved_email,
        invited_user=invited_user,
        privilege=privilege,
        added_by=added_by,
        invitation_code=generate_invitation_code(),
    )
    if roles:
        invitation.roles.set(roles)
    send_invitation_email(invitation)
    log_action(
        AuditAction.INVITATION_SENT,
        actor=inviter,
        entity=invitation,
        project=project,
        metadata={
            "email": invitation.email,
            "invited_user_id": invited_user.id if invited_user else None,
        },
    )
    return invitation


@transaction.atomic
def accept_invitation(*, invitation: Invitation, user) -> Project:
    """Consume an invitation and attach ``user`` to the project."""
    project = invitation.project
    invitation_email = invitation.email

    # Re-use an existing ProjectMember if the user already has one on this
    # project (e.g. previously soft-deleted), otherwise create a new row.
    member = (
        ProjectMember.objects.filter(project=project, users=user).first()
    )
    if member is None:
        member = ProjectMember.objects.create(project=project)
        member.users.add(user)
    else:
        if member.deleted:
            member.deleted = False
            member.save(update_fields=["deleted", "updated_at"])

    # Privilege: only add if the user doesn't already hold an equivalent one.
    if not ProjectPrivilege.objects.filter(
        project=project, user=user, privilege=invitation.privilege
    ).exists():
        ProjectPrivilege.objects.create(
            project=project,
            user=user,
            privilege=invitation.privilege,
        )

    # Attach any roles listed on the invitation.
    role_ids = list(invitation.roles.values_list("id", flat=True))
    if role_ids:
        member.roles.add(*role_ids)

    log_action(
        AuditAction.INVITATION_ACCEPTED,
        actor=user,
        entity=invitation,
        project=project,
        metadata={"email": invitation_email},
    )
    log_action(
        AuditAction.MEMBER_ADDED,
        actor=user,
        project=project,
        metadata={"target_user_id": user.id},
    )

    invitation.delete()

    _notify_pms_of_new_member(project=project, new_user=user)

    return project


def _notify_pms_of_new_member(*, project: Project, new_user) -> None:
    """Send a notification to project managers when a member joins.

    Late-imported from apps.notifications to avoid a circular import
    (notifications imports projects.Project).
    """
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        return

    pm_user_ids = list(
        ProjectPrivilege.objects.filter(
            project=project,
            privilege=MemberPrivileges.PROJECT_MANAGER,
        )
        .exclude(user=new_user)
        .values_list("user_id", flat=True)
        .distinct()
    )
    if not pm_user_ids:
        return

    display = getattr(new_user, "name", None) or getattr(new_user, "email", "A new member")
    create_notification(
        users=pm_user_ids,
        title=f"New member joined {project.name}",
        body=f"{display} accepted their invitation.",
        project=project,
    )


def decline_invitation(*, invitation: Invitation, user=None) -> None:
    project = invitation.project
    invitation_email = invitation.email
    log_action(
        AuditAction.INVITATION_DECLINED,
        actor=user,
        entity=invitation,
        project=project,
        metadata={"email": invitation_email},
    )
    invitation.delete()


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------


class JoinRequestError(Exception):
    """Domain error for join-request creation/transition rules."""


def _is_project_member(*, project: Project, user) -> bool:
    return ProjectPrivilege.objects.filter(project=project, user=user).exists()


def _pm_user_ids(project: Project, exclude=None):
    qs = ProjectPrivilege.objects.filter(
        project=project, privilege=MemberPrivileges.PROJECT_MANAGER
    )
    if exclude is not None:
        qs = qs.exclude(user=exclude)
    return list(qs.values_list("user_id", flat=True).distinct())


@transaction.atomic
def create_join_request(
    *,
    project: Project,
    user,
    requested_privilege: str,
    message: str = "",
) -> JoinRequest:
    """Submit a join request. Refuses if the user is already a member or
    already has a pending request for this project."""
    if _is_project_member(project=project, user=user):
        raise JoinRequestError("You are already a member of this project.")
    if JoinRequest.objects.filter(
        project=project, user=user, status=JoinRequestStatus.PENDING
    ).exists():
        raise JoinRequestError(
            "You already have a pending request for this project."
        )

    join_request = JoinRequest.objects.create(
        project=project,
        user=user,
        requested_privilege=requested_privilege,
        message=message or "",
    )

    log_action(
        AuditAction.JOIN_REQUEST_CREATED,
        actor=user,
        entity=join_request,
        project=project,
        metadata={"requested_privilege": requested_privilege},
    )

    _notify_pms_of_join_request(project=project, join_request=join_request)
    return join_request


@transaction.atomic
def approve_join_request(*, join_request: JoinRequest, approver) -> JoinRequest:
    if join_request.status != JoinRequestStatus.PENDING:
        raise JoinRequestError("This request is no longer pending.")

    project = join_request.project
    requester = join_request.user
    privilege = join_request.requested_privilege

    member = (
        ProjectMember.objects.filter(project=project, users=requester).first()
    )
    if member is None:
        member = ProjectMember.objects.create(project=project)
        member.users.add(requester)
    elif member.deleted:
        member.deleted = False
        member.save(update_fields=["deleted", "updated_at"])

    if not ProjectPrivilege.objects.filter(
        project=project, user=requester, privilege=privilege
    ).exists():
        ProjectPrivilege.objects.create(
            project=project, user=requester, privilege=privilege
        )

    join_request.status = JoinRequestStatus.APPROVED
    join_request.decided_by = approver
    join_request.decided_at = timezone.now()
    join_request.save(
        update_fields=["status", "decided_by", "decided_at", "updated_at"]
    )

    log_action(
        AuditAction.JOIN_REQUEST_APPROVED,
        actor=approver,
        entity=join_request,
        project=project,
        metadata={
            "requester_id": requester.id,
            "privilege": privilege,
        },
    )
    log_action(
        AuditAction.MEMBER_ADDED,
        actor=approver,
        project=project,
        metadata={"target_user_id": requester.id, "via": "join_request"},
    )

    _notify_requester_of_decision(join_request=join_request, approved=True)
    return join_request


@transaction.atomic
def decline_join_request(
    *,
    join_request: JoinRequest,
    approver,
    reason: str = "",
) -> JoinRequest:
    if join_request.status != JoinRequestStatus.PENDING:
        raise JoinRequestError("This request is no longer pending.")

    join_request.status = JoinRequestStatus.DECLINED
    join_request.decided_by = approver
    join_request.decided_at = timezone.now()
    if reason:
        join_request.decision_note = reason
    join_request.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "updated_at",
        ]
    )

    log_action(
        AuditAction.JOIN_REQUEST_DECLINED,
        actor=approver,
        entity=join_request,
        project=join_request.project,
        metadata={"requester_id": join_request.user_id},
    )

    _notify_requester_of_decision(join_request=join_request, approved=False)
    return join_request


@transaction.atomic
def cancel_join_request(*, join_request: JoinRequest, user) -> JoinRequest:
    if join_request.user_id != user.id:
        raise JoinRequestError("Only the requester can cancel this request.")
    if join_request.status != JoinRequestStatus.PENDING:
        raise JoinRequestError("This request is no longer pending.")

    join_request.status = JoinRequestStatus.CANCELLED
    join_request.decided_at = timezone.now()
    join_request.save(
        update_fields=["status", "decided_at", "updated_at"]
    )

    log_action(
        AuditAction.JOIN_REQUEST_CANCELLED,
        actor=user,
        entity=join_request,
        project=join_request.project,
    )
    return join_request


def _notify_pms_of_join_request(
    *, project: Project, join_request: JoinRequest
) -> None:
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        create_notification = None

    pm_ids = _pm_user_ids(project, exclude=join_request.user)
    if pm_ids and create_notification is not None:
        display = (
            getattr(join_request.user, "name", None)
            or getattr(join_request.user, "email", "Someone")
        )
        create_notification(
            users=pm_ids,
            title=f"Join request for {project.name}",
            body=(
                f"{display} requested to join as "
                f"{join_request.get_requested_privilege_display()}."
            ),
            project=project,
        )

    send_join_request_email_to_pms(join_request=join_request, pm_user_ids=pm_ids)


def _notify_requester_of_decision(
    *, join_request: JoinRequest, approved: bool
) -> None:
    try:
        from apps.notifications.services import create_notification
    except Exception:  # pragma: no cover
        create_notification = None

    project = join_request.project
    if create_notification is not None:
        if approved:
            title = f"You joined {project.name}"
            body = "Your request was approved."
        else:
            title = f"Your request for {project.name} was declined"
            body = join_request.decision_note or "Your request was declined."
        create_notification(
            users=[join_request.user_id],
            title=title,
            body=body,
            project=project,
        )

    send_join_request_decision_email(join_request=join_request, approved=approved)
