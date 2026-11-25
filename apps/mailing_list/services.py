"""Service helpers for the mailing list app.

Exposes ``send_mailing_list_message`` — the single entry point used by the
compose view. It collects every current project member (deduped), sends
each one the announcement email, archives a single row, and writes an
audit log entry.
"""

from __future__ import annotations

from typing import Iterable

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.audit.services import AuditAction, log_action
from apps.communications.models import EmailKind
from apps.communications.services import send_logged
from apps.core.emails import build_message, render_email
from apps.projects.models import ProjectMember

from .models import MailingListMessage


def _collect_recipients(project, *, exclude_user=None) -> list:
    """Return the deduplicated list of User instances for a project.

    Walks ``ProjectMember.users`` (which is an M2M) over every non-deleted
    membership row. Excludes ``exclude_user`` — typically the sender, who
    composed the message and doesn't need a copy.
    """
    member_user_ids = ProjectMember.objects.filter(
        project=project, deleted=False
    ).values_list("users__id", flat=True)
    unique_ids = {uid for uid in member_user_ids if uid}
    if exclude_user is not None and getattr(exclude_user, "pk", None):
        unique_ids.discard(exclude_user.pk)
    if not unique_ids:
        return []
    User = get_user_model()
    return list(User.objects.filter(pk__in=unique_ids, is_active=True))


def _send_one(*, recipient, sender, project, context: dict) -> None:
    """Dispatch a single email with ``reply_to`` set to the sender's inbox."""
    reply_to = [sender.email] if sender and getattr(sender, "email", "") else None
    msg = build_message(
        render_email("mailing_list", context), [recipient.email], reply_to=reply_to
    )
    send_logged(
        msg, kind=EmailKind.MAILING_LIST, project=project, triggered_by=sender
    )


@transaction.atomic
def send_mailing_list_message(
    *,
    project,
    sender,
    subject: str,
    body: str,
) -> MailingListMessage:
    """Send a project announcement to every project member and archive it.

    Returns the archived :class:`MailingListMessage`. Its
    ``recipient_count`` reflects the number of unique, active users that
    were mailed (excluding the sender themselves).
    """
    recipients: Iterable = _collect_recipients(project, exclude_user=sender)

    for recipient in recipients:
        context = {
            "recipient": recipient,
            "sender": sender,
            "project": project,
            "subject": subject,
            "body": body,
        }
        _send_one(recipient=recipient, sender=sender, project=project, context=context)

    message = MailingListMessage.objects.create(
        project=project,
        sender=sender,
        subject=subject,
        body=body,
        recipient_count=len(recipients),
    )

    log_action(
        AuditAction.MAILING_LIST_MESSAGE_SENT,
        actor=sender,
        entity=message,
        project=project,
        metadata={
            "subject": subject,
            "recipient_count": len(recipients),
        },
    )
    return message
