"""Sending an email and recording that it was sent.

:func:`send_logged` is the one place a project email should leave from: it
sends the message and writes an :class:`~apps.communications.models.EmailLog`
row per recipient, so the project's Leads can later see what went out, to
whom and when. Account emails (confirmation, password reset, lockout,
welcome) do not go through it — they are nobody's business but the account
holder's.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.functions import Lower

from .models import EmailLog, EmailStatus

logger = logging.getLogger(__name__)


def send_logged(
    message,
    *,
    kind: str,
    project=None,
    triggered_by=None,
    fail_silently: bool = False,
) -> int:
    """Send ``message`` and log one row per address in ``message.to``.

    ``triggered_by`` is the member whose action sent it, if any — None for
    an email i-CONNECTS sends on its own.

    Returns the number of messages the backend accepted, as
    ``EmailMessage.send`` does. A send that raises is logged as failed with
    the error, then re-raised unless ``fail_silently``. Writing the log never
    breaks the send: if the rows cannot be saved the email has still gone
    (or failed) exactly as it would have without logging.
    """
    error = ""
    try:
        sent = message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised below
        sent = 0
        error = f"{type(exc).__name__}: {exc}"[:1000]
        if fail_silently:
            logger.warning("send_logged: %s email failed: %s", kind, error)
        _record(
            message, kind=kind, project=project, triggered_by=triggered_by, error=error
        )
        if not fail_silently:
            raise
        return 0
    if not sent:
        error = "The mail backend did not accept the message."
    _record(
        message, kind=kind, project=project, triggered_by=triggered_by, error=error
    )
    return sent


def _html_alternative(message) -> str:
    for content, mimetype in getattr(message, "alternatives", None) or []:
        if mimetype == "text/html":
            return str(content)
    return ""


def _record(message, *, kind, project, triggered_by, error) -> None:
    addresses = [a for a in (message.to or []) if a]
    if not addresses:
        return
    # The email itself is kept only where a Lead can read it: in a project's
    # log. An email about no project is in no one's log.
    content = {}
    if project is not None:
        content = {
            "from_email": (message.from_email or "")[:255],
            "reply_to": ", ".join(message.reply_to or [])[:255],
            "text_body": str(message.body or ""),
            "html_body": _html_alternative(message),
        }
    try:
        # A savepoint, so a failed write cannot poison a caller's transaction.
        with transaction.atomic():
            users = {
                u.email.lower(): u
                for u in get_user_model()
                .objects.annotate(email_lower=Lower("email"))
                .filter(email_lower__in=[a.lower() for a in addresses])
            }
            EmailLog.objects.bulk_create(
                EmailLog(
                    kind=kind,
                    to_email=address,
                    recipient=users.get(address.lower()),
                    project=project,
                    triggered_by=triggered_by,
                    subject=(message.subject or "")[:255],
                    status=EmailStatus.FAILED if error else EmailStatus.SENT,
                    error=error,
                    **content,
                )
                for address in addresses
            )
    except Exception:  # noqa: BLE001 - logging must never break a send
        logger.exception("send_logged: could not record %s email", kind)


def logs_for_user(queryset, user):
    """Narrow ``queryset`` to the emails that went to ``user``.

    Matches the linked account and, for rows written before the account
    existed (an invitation to a bare address), the address itself.
    """
    return queryset.annotate(to_lower=Lower("to_email")).filter(
        Q(recipient=user) | Q(recipient__isnull=True, to_lower=(user.email or "").lower())
    )


def last_sent_by_user(project, users) -> dict:
    """``{user.pk: datetime}`` of the last email sent to each of ``users``
    about ``project``. Users who were never emailed are absent."""
    users = [u for u in users if u is not None]
    if not users:
        return {}
    sent = EmailLog.objects.filter(project=project, status=EmailStatus.SENT)
    by_id = dict(
        sent.filter(recipient__in=users)
        .values_list("recipient_id")
        .annotate(last=Max("sent_at"))
    )
    by_address = dict(
        sent.filter(recipient__isnull=True)
        .annotate(to_lower=Lower("to_email"))
        .filter(to_lower__in=[(u.email or "").lower() for u in users])
        .values_list("to_lower")
        .annotate(last=Max("sent_at"))
    )
    result = {}
    for user in users:
        times = [
            t
            for t in (by_id.get(user.pk), by_address.get((user.email or "").lower()))
            if t is not None
        ]
        if times:
            result[user.pk] = max(times)
    return result
