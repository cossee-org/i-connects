"""Celery tasks for the accounts app.

These are thin wrappers that push user-facing mail off the request path so
the caller returns immediately. Synchronous helpers still live in
``apps.accounts.emails``.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from apps.core.emails import send_templated_email

logger = logging.getLogger(__name__)


@shared_task(name="accounts.send_welcome_email")
def send_welcome_email_task(user_id: int, site_url: str = "/") -> None:
    """Send the post-signup welcome email to ``user_id``."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_welcome_email_task: user %s not found", user_id)
        return

    # Prefer the app-specific templates authored in Phase 3A; fall back to
    # the shared ones under templates/emails/ if they are missing.
    context = {"user": user, "site_url": site_url}
    try:
        render_to_string("accounts/emails/welcome_subject.txt", context)
        subject_template = "accounts/emails/welcome_subject.txt"
        html_template = "accounts/emails/welcome.html"
        text_template = "accounts/emails/welcome.txt"
    except Exception:
        subject_template = "emails/welcome/subject.txt"
        html_template = "emails/welcome/body.html"
        text_template = "emails/welcome/body.txt"

    send_templated_email(
        subject_template=subject_template,
        html_template=html_template,
        text_template=text_template,
        context=context,
        to=[user.email],
    )


@shared_task(name="accounts.send_password_reset_email")
def send_password_reset_email_task(user_id: int, reset_url: str) -> None:
    """Send a pre-rendered password reset URL to ``user_id``."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_password_reset_email_task: user %s not found", user_id)
        return

    context = {"user": user, "reset_url": reset_url}
    send_templated_email(
        subject_template="accounts/emails/password_reset_subject.txt",
        html_template="accounts/emails/password_reset.html",
        text_template="accounts/emails/password_reset.txt",
        context=context,
        to=[user.email],
    )
