"""Signal handlers for authentication events.

Django's ``django.contrib.auth.signals`` provide three signals we care
about: ``user_logged_in``, ``user_logged_out``, and ``user_login_failed``.
Using signals (instead of explicit calls from our own login view) means
third-party backends — allauth / ORCID OAuth — are logged too, without us
having to hook into each of them individually.
"""

from __future__ import annotations

from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .models import AuditAction
from .services import log_action


def _ip(request) -> str:
    if request is None:
        return ""
    return request.META.get("REMOTE_ADDR", "")


@receiver(user_logged_in)
def _on_user_logged_in(sender, request, user, **kwargs):
    log_action(AuditAction.USER_LOGIN, actor=user, metadata={"ip": _ip(request)})


@receiver(user_logged_out)
def _on_user_logged_out(sender, request, user, **kwargs):
    # ``user`` may be None if the session expired before logout fires.
    log_action(AuditAction.USER_LOGOUT, actor=user, metadata={"ip": _ip(request)})


@receiver(user_login_failed)
def _on_user_login_failed(sender, credentials, request=None, **kwargs):
    # Never record the password; keep only the identifier the client tried.
    safe_credentials = {}
    if credentials:
        identifier = credentials.get("username") or credentials.get("email")
        if identifier:
            safe_credentials["identifier"] = identifier
    log_action(
        AuditAction.USER_LOGIN_FAILED,
        actor=None,
        metadata={"ip": _ip(request), **safe_credentials},
    )
