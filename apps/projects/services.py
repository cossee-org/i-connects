"""Helper services for the projects app.

Small, procedural functions -- deliberately not a service layer. Keeps the
view code easy to read.
"""

from __future__ import annotations

import secrets

from django.db import transaction

from apps.core.enums import MemberPrivileges

from .emails import send_invitation_email
from .models import Invitation, Project, ProjectMember, ProjectPrivilege


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
    return project


@transaction.atomic
def create_invitation(
    *,
    project: Project,
    email: str,
    privilege: str,
    added_by: str,
    roles=None,
) -> Invitation:
    """Create an invitation and fire the email."""
    invitation = Invitation.objects.create(
        project=project,
        email=email.lower(),
        privilege=privilege,
        added_by=added_by,
        invitation_code=generate_invitation_code(),
    )
    if roles:
        invitation.roles.set(roles)
    send_invitation_email(invitation)
    return invitation


@transaction.atomic
def accept_invitation(*, invitation: Invitation, user) -> Project:
    """Consume an invitation and attach ``user`` to the project."""
    project = invitation.project

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


def decline_invitation(*, invitation: Invitation) -> None:
    invitation.delete()
