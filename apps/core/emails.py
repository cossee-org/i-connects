"""Shared email helper used by Celery tasks across apps."""

from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def absolute_url(path: str) -> str:
    """Prefix ``path`` with ``settings.SITE_URL`` for a link in an email.

    Emails are sent from Celery tasks, where there is no request to build an
    absolute URI from. An unset ``SITE_URL`` leaves the bare path, which mail
    clients mangle into a broken link, so it is logged rather than silent.
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


def send_templated_email(
    subject_template: str,
    html_template: str,
    text_template: str,
    context: dict,
    to: Iterable[str],
    from_email: str | None = None,
    *,
    kind: str | None = None,
    project=None,
    triggered_by=None,
) -> None:
    """Render three templates (subject, HTML body, text body) and send.

    Pass ``kind`` (an ``EmailKind``) for an email a project's Leads should be
    able to see in the email log, with the ``project`` it is about if there
    is one and the member whose action sent it (``triggered_by``) if any. Without ``kind`` the email is sent unlogged, which is right for
    account emails only.

    ``to`` may be a list of strings or any iterable of email addresses.
    Subjects are stripped + collapsed to one line to prevent header injection.
    """
    subject = render_to_string(subject_template, context).strip().replace("\n", " ")
    html_body = render_to_string(html_template, context)
    text_body = render_to_string(text_template, context)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=list(to),
    )
    msg.attach_alternative(html_body, "text/html")
    if kind is None:
        msg.send()
        return
    # Imported here: communications depends on core, not the other way round.
    from apps.communications.services import send_logged

    send_logged(msg, kind=kind, project=project, triggered_by=triggered_by)
