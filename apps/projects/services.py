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
    # Seed the default Kanban columns so a new board is ready to use.
    # Imported locally to avoid a circular import with the tasks app.
    from apps.tasks.models import KanbanBoard

    KanbanBoard.objects.bulk_create(
        [
            KanbanBoard(project=project, name=name, container_order=order)
            for order, name in enumerate(("To Do", "In Progress", "Done"))
        ]
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
def resend_invitation(*, invitation: Invitation, resender=None) -> Invitation:
    """Regenerate the invitation's code and re-send the email.

    Regenerating the code invalidates whatever link was in the original
    email — the right default for a resend, since the usual reason to
    resend is that the first email never arrived, was broken (e.g. a
    misconfigured link), or was lost. ``created_at`` is bumped too, so the
    invitation resurfaces at the top of the pending list.
    """
    invitation.invitation_code = generate_invitation_code()
    invitation.created_at = timezone.now()
    invitation.save(update_fields=["invitation_code", "created_at"])
    send_invitation_email(invitation)
    log_action(
        AuditAction.INVITATION_RESENT,
        actor=resender,
        entity=invitation,
        project=invitation.project,
        metadata={"email": invitation.email},
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
        route="projects:members",
        route_kwargs={"pk": project.pk},
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
# "Last Project Manager" invariant
# ---------------------------------------------------------------------------


class LastProjectManagerError(Exception):
    """Refused: would leave the project with zero Project Managers.

    Django superuser/admin operations bypass this rule by design — admin is
    god mode. Enforce only in user-facing flows.
    """


def _another_pm_exists(project: Project, *, excluding_user) -> bool:
    return (
        ProjectPrivilege.objects.filter(
            project=project, privilege=MemberPrivileges.PROJECT_MANAGER
        )
        .exclude(user=excluding_user)
        .exists()
    )


def is_last_project_manager(*, project: Project, user) -> bool:
    """Return True if ``user`` currently holds PROJECT_MANAGER on ``project``
    and no other user holds it."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    is_pm = ProjectPrivilege.objects.filter(
        project=project,
        user=user,
        privilege=MemberPrivileges.PROJECT_MANAGER,
    ).exists()
    if not is_pm:
        return False
    return not _another_pm_exists(project, excluding_user=user)


def assert_pm_self_action_allowed(
    *,
    project: Project,
    actor,
    target_user,
    new_privilege: str | None,
) -> None:
    """Refuse a self-targeted demotion / removal that would leave the
    project with zero Project Managers.

    Parameters
    ----------
    new_privilege
        The privilege the target will hold after the action. ``None`` means
        the user is being removed from the project entirely. If the target
        will still be a PROJECT_MANAGER, the action is always allowed.
    """
    if actor is None or target_user is None:
        return
    if getattr(actor, "id", None) != getattr(target_user, "id", None):
        # Acting on someone else — the actor (a PM) remains, so the
        # invariant cannot be broken by this single action.
        return
    if new_privilege == MemberPrivileges.PROJECT_MANAGER:
        return
    if not is_last_project_manager(project=project, user=actor):
        return
    raise LastProjectManagerError(
        "A project must always have at least one Project Manager. "
        "Promote another member first."
    )


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
            # The project's own queue rather than this one request's review
            # page: by the time a PM clicks, another PM may already have
            # decided it, and the list still shows the outcome.
            route="projects:join_requests",
            route_kwargs={"pk": project.pk},
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
            # They are a member now, so the project itself is the useful
            # destination.
            route, route_kwargs = "projects:detail", {"pk": project.pk}
        else:
            title = f"Your request for {project.name} was declined"
            body = join_request.decision_note or "Your request was declined."
            # A declined requester cannot open the project, so send them to
            # their own list of requests.
            route, route_kwargs = "projects:my_join_requests", None
        create_notification(
            users=[join_request.user_id],
            title=title,
            body=body,
            project=project,
            route=route,
            route_kwargs=route_kwargs,
        )

    send_join_request_decision_email(join_request=join_request, approved=approved)


def get_user_public_projects(user) -> list[Project]:
    """Public projects ``user`` belongs to, newest activity first.

    Used by the researcher profile. Only public projects are listed: a
    private project's existence should never be disclosed by someone's
    membership in it, so the filter is on the project, not on the viewer.

    Each returned project carries an ``is_lead`` attribute — whether the
    person manages it — so the profile can label the row without a query
    per project.
    """
    privileges = (
        ProjectPrivilege.objects.filter(user=user, project__is_public=True)
        .select_related("project")
        # ``-created_at`` last so that where a user somehow holds two
        # privilege rows on one project, the newest one wins — matching
        # how the project list resolves the same ambiguity.
        .order_by("-project__updated_at", "project_id", "-created_at")
    )
    projects: list[Project] = []
    seen: set[int] = set()
    for privilege in privileges:
        if privilege.project_id in seen:
            continue
        seen.add(privilege.project_id)
        project = privilege.project
        project.is_lead = privilege.privilege == MemberPrivileges.PROJECT_MANAGER
        projects.append(project)
    return projects
