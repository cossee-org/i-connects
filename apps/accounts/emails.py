"""Email helpers for the accounts app.

All emails are rendered from templates under ``templates/accounts/emails/``
and sent via Django's configured ``EMAIL_BACKEND`` (console in dev).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import email_confirmation_token, password_reset_token


def _render_subject(template_name: str, context: dict) -> str:
    # Subjects are a single line; strip to avoid header injection from stray newlines.
    return render_to_string(template_name, context).strip().replace("\n", " ")


def _send(
    subject: str,
    text_body: str,
    html_body: str,
    to_email: str,
) -> None:
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def send_welcome_email(user, *, request=None) -> None:
    """Send the post-signup welcome email."""
    site_url = request.build_absolute_uri("/") if request is not None else "/"
    ctx = {"user": user, "site_url": site_url}
    subject = _render_subject("accounts/emails/welcome_subject.txt", ctx)
    text_body = render_to_string("accounts/emails/welcome.txt", ctx)
    html_body = render_to_string("accounts/emails/welcome.html", ctx)
    _send(subject, text_body, html_body, user.email)


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
    ctx = {"user": user, "confirm_url": confirm_url}
    subject = _render_subject("accounts/emails/confirmation_subject.txt", ctx)
    text_body = render_to_string("accounts/emails/confirmation.txt", ctx)
    html_body = render_to_string("accounts/emails/confirmation.html", ctx)
    _send(subject, text_body, html_body, user.email)


def send_lockout_email(user, *, raw_token: str, request=None) -> None:
    """Send a one-time unlock link to a locked-out account."""
    path = reverse("accounts:lockout_unlock", kwargs={"token": raw_token})
    unlock_url = request.build_absolute_uri(path) if request is not None else path
    ctx = {"user": user, "unlock_url": unlock_url}
    subject = _render_subject("accounts/emails/lockout_subject.txt", ctx)
    text_body = render_to_string("accounts/emails/lockout.txt", ctx)
    html_body = render_to_string("accounts/emails/lockout.html", ctx)
    _send(subject, text_body, html_body, user.email)


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
    ctx = {"user": user, "reset_url": reset_url}
    subject = _render_subject("accounts/emails/password_reset_subject.txt", ctx)
    text_body = render_to_string("accounts/emails/password_reset.txt", ctx)
    html_body = render_to_string("accounts/emails/password_reset.html", ctx)
    _send(subject, text_body, html_body, user.email)
