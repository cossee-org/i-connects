"""Celery tasks for the accounts app.

These are thin wrappers that push user-facing mail off the request path so
the caller returns immediately. Synchronous helpers still live in
``apps.accounts.emails``.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.contrib.auth import get_user_model

from apps.core.emails import absolute_url, send_email

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

    # The default "/" (a caller with no request) is no use in an inbox.
    if "://" not in site_url:
        site_url = absolute_url("/")
    send_email("welcome", {"user": user, "site_url": site_url}, [user.email])


@shared_task(name="accounts.send_password_reset_email")
def send_password_reset_email_task(user_id: int, reset_url: str) -> None:
    """Send a pre-rendered password reset URL to ``user_id``."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_password_reset_email_task: user %s not found", user_id)
        return

    send_email("password_reset", {"user": user, "reset_url": reset_url}, [user.email])
