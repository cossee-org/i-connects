"""Invitation email helpers.

Uses Django's ``send_mail`` + ``render_to_string``. The dev email backend
is console-based, so this is visible in the runserver output.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse


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
    site = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{site}{path}" if site else path


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
