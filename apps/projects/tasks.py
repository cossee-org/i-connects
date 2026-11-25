"""Celery tasks for the projects app."""

from __future__ import annotations

import logging

from celery import shared_task
from apps.communications.models import EmailKind
from apps.core.emails import send_email

from .emails import _invitation_url

logger = logging.getLogger(__name__)


@shared_task(name="projects.send_invitation_email")
def send_invitation_email_task(invitation_id: int, inviter_id: int | None = None) -> None:
    """Email the invitee with the accept/decline link.

    ``inviter_id`` is the Lead who sent it, recorded in the email log.
    """
    from django.contrib.auth import get_user_model

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
    send_email(
        "invitation",
        context,
        [invitation.email],
        kind=EmailKind.INVITATION,
        project=invitation.project,
        triggered_by=(
            get_user_model().objects.filter(pk=inviter_id).first() if inviter_id else None
        ),
    )
