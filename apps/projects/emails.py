"""Project emails: invitations and join requests.

Each is a folder under ``templates/emails/`` sent with
:func:`apps.core.emails.send_email` and recorded in the project's email log.
"""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from apps.communications.models import EmailKind
from apps.core.emails import absolute_url, send_email


def _send(*, name, context, recipients, kind, project, triggered_by=None) -> None:
    """Send one project email and record it in the project's email log.

    One message per recipient, so nobody sees the others' addresses and each
    send succeeds or fails, and is logged, on its own. A failure never
    reaches the caller: these emails go out in the middle of a request, and
    an invitation or a decision must not be lost because the mail server is
    down. The failure is in the email log instead.
    """
    for recipient in recipients:
        send_email(
            name,
            context,
            [recipient],
            kind=kind,
            project=project,
            triggered_by=triggered_by,
            fail_silently=True,
        )


def _invitation_url(invitation) -> str:
    try:
        path = reverse(
            "projects:accept_or_decline",
            kwargs={"code": invitation.invitation_code},
        )
    except NoReverseMatch:
        # Phase 5 may not yet have wired the project urls into the root
        # urlconf; fall back to a bare path so the message is still useful.
        path = f"/invites/{invitation.invitation_code}/"
    return absolute_url(path)


def send_invitation_email(invitation, *, triggered_by=None) -> None:
    """Email ``invitation.email`` the accept/decline link.

    ``triggered_by`` is the Lead who sent (or resent) the invitation.
    """
    context = {
        "invitation": invitation,
        "project": invitation.project,
        "url": _invitation_url(invitation),
    }
    _send(
        name="invitation",
        context=context,
        recipients=[invitation.email],
        kind=EmailKind.INVITATION,
        project=invitation.project,
        triggered_by=triggered_by,
    )


def _project_join_requests_url(project) -> str:
    try:
        path = reverse("projects:join_requests", kwargs={"pk": project.pk})
    except NoReverseMatch:
        path = f"/projects/{project.pk}/join-requests/"
    return absolute_url(path)


def send_join_request_email_to_pms(*, join_request, pm_user_ids) -> None:
    """Email project managers when a new join request lands.

    Recipients are resolved from ``pm_user_ids`` rather than fetching the
    privilege rows again — the caller already has them.
    """
    if not pm_user_ids:
        return
    from django.contrib.auth import get_user_model

    User = get_user_model()
    recipients = list(
        User.objects.filter(pk__in=pm_user_ids, email__isnull=False)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if not recipients:
        return

    project = join_request.project
    context = {
        "join_request": join_request,
        "project": project,
        "requester": join_request.user,
        "url": _project_join_requests_url(project),
    }
    _send(
        name="join_request",
        context=context,
        recipients=recipients,
        kind=EmailKind.JOIN_REQUEST,
        project=project,
        triggered_by=join_request.user,
    )


def _project_detail_url(project) -> str:
    try:
        path = reverse("projects:detail", kwargs={"pk": project.pk})
    except NoReverseMatch:
        path = f"/projects/{project.pk}/"
    return absolute_url(path)


def send_join_request_decision_email(*, join_request, approved: bool) -> None:
    """Email the requester when a PM approves or declines their request."""
    recipient = getattr(join_request.user, "email", None)
    if not recipient:
        return
    project = join_request.project
    context = {
        "join_request": join_request,
        "project": project,
        "approved": approved,
        "url": _project_detail_url(project),
    }
    _send(
        name="join_request_approved" if approved else "join_request_declined",
        context=context,
        recipients=[recipient],
        kind=EmailKind.JOIN_REQUEST_DECISION,
        project=project,
        # The Lead who approved or declined.
        triggered_by=join_request.decided_by,
    )
