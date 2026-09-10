"""Invitation email helpers.

Uses Django's ``send_mail`` + ``render_to_string``. The dev email backend
is console-based, so this is visible in the runserver output.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse

logger = logging.getLogger(__name__)


def _absolute_url(path: str) -> str:
    """Prefix ``path`` with ``settings.SITE_URL``.

    These emails are sent from request-less contexts (services, Celery
    tasks), so there's no ``request.build_absolute_uri`` to fall back on.
    An unset ``SITE_URL`` would otherwise silently degrade to a bare
    relative path that mail clients mangle into a broken link (e.g.
    ``http:///invites/<code>/``) — log loudly instead.
    """
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    if not site:
        logger.warning(
            "SITE_URL is not set — email link for %s will be a bare "
            "relative path instead of an absolute URL.",
            path,
        )
        return path
    return f"{site}{path}"


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
    return _absolute_url(path)


def send_invitation_email(invitation) -> None:
    """Email ``invitation.email`` the accept/decline link."""
    context = {
        "invitation": invitation,
        "project": invitation.project,
        "url": _invitation_url(invitation),
    }
    subject = render_to_string("projects/emails/invitation_subject.txt", context).strip()
    body_text = render_to_string("projects/emails/invitation.txt", context)
    body_html = render_to_string("projects/emails/invitation.html", context)
    send_mail(
        subject=subject,
        message=body_text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[invitation.email],
        html_message=body_html,
        fail_silently=True,
    )


def _project_join_requests_url(project) -> str:
    try:
        path = reverse("projects:join_requests", kwargs={"pk": project.pk})
    except NoReverseMatch:
        path = f"/projects/{project.pk}/join-requests/"
    return _absolute_url(path)


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
    subject = render_to_string(
        "projects/emails/join_request_subject.txt", context
    ).strip()
    body_text = render_to_string("projects/emails/join_request.txt", context)
    body_html = render_to_string("projects/emails/join_request.html", context)
    send_mail(
        subject=subject,
        message=body_text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=recipients,
        html_message=body_html,
        fail_silently=True,
    )


def _project_detail_url(project) -> str:
    try:
        path = reverse("projects:detail", kwargs={"pk": project.pk})
    except NoReverseMatch:
        path = f"/projects/{project.pk}/"
    return _absolute_url(path)


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
    template_stem = (
        "join_request_approved" if approved else "join_request_declined"
    )
    subject = render_to_string(
        f"projects/emails/{template_stem}_subject.txt", context
    ).strip()
    body_text = render_to_string(
        f"projects/emails/{template_stem}.txt", context
    )
    body_html = render_to_string(
        f"projects/emails/{template_stem}.html", context
    )
    send_mail(
        subject=subject,
        message=body_text,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[recipient],
        html_message=body_html,
        fail_silently=True,
    )
