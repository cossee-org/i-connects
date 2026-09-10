"""Celery tasks for the projects app."""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.urls import NoReverseMatch, reverse

from apps.core.emails import send_templated_email

logger = logging.getLogger(__name__)


def _invitation_url(invitation) -> str:
    try:
        path = reverse(
            "projects:accept_or_decline",
            kwargs={"code": invitation.invitation_code},
        )
    except NoReverseMatch:
        path = f"/invites/{invitation.invitation_code}/"
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    if not site:
        logger.warning(
            "SITE_URL is not set — invitation email link will be a bare "
            "relative path instead of an absolute URL."
        )
        return path
    return f"{site}{path}"


@shared_task(name="projects.send_invitation_email")
def send_invitation_email_task(invitation_id: int) -> None:
    """Email the invitee with the accept/decline link."""
    from .models import Invitation

    try:
        invitation = Invitation.objects.select_related("project").get(pk=invitation_id)
    except Invitation.DoesNotExist:
        logger.warning("send_invitation_email_task: invitation %s not found", invitation_id)
        return

    context = {
        "invitation": invitation,
        "project": invitation.project,
        "url": _invitation_url(invitation),
    }
    send_templated_email(
        subject_template="projects/emails/invitation_subject.txt",
        html_template="projects/emails/invitation.html",
        text_template="projects/emails/invitation.txt",
        context=context,
        to=[invitation.email],
    )
