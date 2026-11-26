"""Email helpers for the accounts app.

Each email is a folder under ``templates/emails/`` (``welcome``,
``confirm_email``, ``password_reset``, ``account_locked``) sent with
:func:`apps.core.emails.send_email`. Account emails are not written to any
project's email log.
"""

from __future__ import annotations

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.emails import absolute_url, send_email

from .tokens import email_confirmation_token, password_reset_token


def send_welcome_email(user, *, request=None) -> None:
    """Send the post-signup welcome email."""
    site_url = request.build_absolute_uri("/") if request is not None else absolute_url("/")
    send_email("welcome", {"user": user, "site_url": site_url}, [user.email])


def send_confirmation_email(
    user,
    *,
    request=None,
    token_generator: PasswordResetTokenGenerator | None = None,
) -> None:
    """Send the signup confirmation link to ``user.email``.

    Required before the account can log in — see
    ``apps.accounts.forms.LoginForm.clean``.
    """
    generator = token_generator or email_confirmation_token
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = generator.make_token(user)
    path = reverse(
        "accounts:email_confirm",
        kwargs={"uidb64": uidb64, "token": token},
    )
    confirm_url = request.build_absolute_uri(path) if request is not None else path
    send_email("confirm_email", {"user": user, "confirm_url": confirm_url}, [user.email])


def send_lockout_email(user, *, raw_token: str, request=None) -> None:
    """Send a one-time unlock link to a locked-out account."""
    path = reverse("accounts:lockout_unlock", kwargs={"token": raw_token})
    unlock_url = request.build_absolute_uri(path) if request is not None else path
    send_email("account_locked", {"user": user, "unlock_url": unlock_url}, [user.email])


def send_password_reset_email(
    user,
    *,
    request=None,
    token_generator: PasswordResetTokenGenerator | None = None,
) -> None:
    """Send a password-reset link to ``user.email``.

    The link embeds a URL-safe base64-encoded user id plus a signed token
    produced by :data:`apps.accounts.tokens.password_reset_token`.
    """
    generator = token_generator or password_reset_token
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = generator.make_token(user)
    path = reverse(
        "accounts:password_reset_confirm",
        kwargs={"uidb64": uidb64, "token": token},
    )
    reset_url = request.build_absolute_uri(path) if request is not None else path
    send_email("password_reset", {"user": user, "reset_url": reset_url}, [user.email])
