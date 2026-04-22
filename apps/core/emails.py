"""Shared email helper used by Celery tasks across apps."""

from __future__ import annotations

from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_templated_email(
    subject_template: str,
    html_template: str,
    text_template: str,
    context: dict,
    to: Iterable[str],
    from_email: str | None = None,
) -> None:
    """Render three templates (subject, HTML body, text body) and send.

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
    msg.send()
