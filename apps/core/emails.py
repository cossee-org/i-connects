"""Rendering and sending i-CONNECTS email.

Every email lives in its own folder under ``templates/emails/<name>/`` with
a ``subject.txt``, a ``body.txt`` and a ``body.html``, and both bodies extend
the shared base templates so every email has the same look. Send one with
:func:`send_email`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import Context
from django.template.loader import get_template, render_to_string

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


def _render_plain(template_name: str, context: dict) -> str:
    """Render a text template with autoescaping off.

    Subjects and plain-text bodies are not HTML: a task called ``Q&A`` must
    read ``Q&A``, not ``Q&amp;A``.
    """
    return get_template(template_name).template.render(
        Context(context, autoescape=False)
    )


def _tidy_text(text: str) -> str:
    """Strip trailing spaces and collapse the blank lines template tags leave."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)) + "\n"


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def render_templates(
    subject_template: str, html_template: str, text_template: str, context: dict
) -> RenderedEmail:
    """Render a subject, a plain-text body and an HTML body.

    Subjects are stripped and collapsed to one line to prevent header
    injection.
    """
    subject = " ".join(_render_plain(subject_template, context).split())
    return RenderedEmail(
        subject=subject,
        text=_tidy_text(_render_plain(text_template, context)),
        html=render_to_string(html_template, context),
    )


def render_email(name: str, context: dict) -> RenderedEmail:
    """Render the email in ``templates/emails/<name>/``.

    Each email is a folder holding ``subject.txt``, ``body.txt`` and
    ``body.html``; both bodies extend the shared base templates.
    """
    return render_templates(
        f"emails/{name}/subject.txt",
        f"emails/{name}/body.html",
        f"emails/{name}/body.txt",
        context,
    )


def build_message(
    rendered: RenderedEmail,
    to: Iterable[str],
    *,
    from_email: str | None = None,
    reply_to: Iterable[str] | None = None,
) -> EmailMultiAlternatives:
    message = EmailMultiAlternatives(
        subject=rendered.subject,
        body=rendered.text,
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        to=list(to),
        reply_to=list(reply_to) if reply_to else None,
    )
    message.attach_alternative(rendered.html, "text/html")
    return message


def _dispatch(message, *, kind, project, triggered_by, fail_silently) -> None:
    if kind is None:
        message.send(fail_silently=fail_silently)
        return
    # Imported here: communications depends on core, not the other way round.
    from apps.communications.services import send_logged

    send_logged(
        message,
        kind=kind,
        project=project,
        triggered_by=triggered_by,
        fail_silently=fail_silently,
    )


def send_email(
    name: str,
    context: dict,
    to: Iterable[str],
    *,
    reply_to: Iterable[str] | None = None,
    kind: str | None = None,
    project=None,
    triggered_by=None,
    fail_silently: bool = False,
) -> None:
    """Render ``templates/emails/<name>/`` and send it to ``to``.

    This is how every i-CONNECTS email leaves. Pass ``kind`` (an
    ``EmailKind``) for an email a project's Leads should be able to see in
    the email log, with the ``project`` it is about if there is one and the
    member whose action sent it (``triggered_by``) if any. Without ``kind``
    the email is sent unlogged, which is right for account emails only.
    """
    message = build_message(render_email(name, context), to, reply_to=reply_to)
    _dispatch(
        message,
        kind=kind,
        project=project,
        triggered_by=triggered_by,
        fail_silently=fail_silently,
    )
