"""Allauth adapters for the accounts app.

The ORCID flow has two desired behaviours:

1. If the visitor is already logged in, connecting their ORCID should attach
   it to the existing account rather than creating a new one.
2. If the visitor is anonymous and no matching account exists, allauth will
   create a new ``User`` from the ORCID profile. We populate ``name`` and
   ``orcid`` from the returned profile data.
"""

from __future__ import annotations

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from apps.core.emails import send_email

User = get_user_model()
logger = logging.getLogger(__name__)


def _allauth_context(email: str, context: dict) -> dict:
    """Map allauth's email context onto the names our templates use."""
    return {
        "email": email,
        "user": context.get("user"),
        "confirm_url": context.get("activate_url"),
        "reset_url": context.get("password_reset_url"),
        "password_reset_url": context.get("password_reset_url"),
        "signup_url": context.get("signup_url"),
    }


# allauth template prefix -> (our email, extra context). Each sends the
# i-CONNECTS email in templates/emails/<name>/ instead of allauth's
# plain-text default.
ALLAUTH_EMAILS = {
    "account/email/email_confirmation_signup": ("confirm_email", {}),
    "account/email/email_confirmation": ("confirm_email", {"adding_address": True}),
    "account/email/password_reset_key": ("password_reset", {}),
    "account/email/unknown_account": ("unknown_account", {}),
    "account/email/account_already_exists": ("account_already_exists", {}),
}


class AccountAdapter(DefaultAccountAdapter):
    """Account adapter: the populated user, and every allauth email."""

    def send_mail(self, template_prefix, email, context) -> None:
        """Send allauth's account emails in the i-CONNECTS email template.

        allauth sends its own emails for ORCID sign-up confirmation, adding
        an address, and its password reset pages. Left alone they go out
        as plain text signed "example.com". Known ones map onto our emails
        (``ALLAUTH_EMAILS``); any other is wrapped in the ``account_notice``
        email so it still carries the i-CONNECTS layout, and logged so it
        can be given a template of its own.
        """
        recipients = [email] if isinstance(email, str) else list(email)
        address = recipients[0] if recipients else ""
        mapped = ALLAUTH_EMAILS.get(template_prefix)
        # A confirmation by code has no link for our button to point at.
        if mapped and template_prefix.startswith("account/email/email_confirmation"):
            if not context.get("activate_url"):
                mapped = None
        if mapped:
            name, extra = mapped
            send_email(name, {**_allauth_context(address, context), **extra}, recipients)
            return

        logger.warning(
            "allauth email %s has no i-CONNECTS template; sending it as an "
            "account notice.",
            template_prefix,
        )
        ctx = {"email": address, **context}
        subject = " ".join(
            render_to_string(f"{template_prefix}_subject.txt", ctx).split()
        )
        message = render_to_string(f"{template_prefix}_message.txt", ctx).strip()
        send_email("account_notice", {"subject": subject, "message": message}, recipients)

    def populate_username(self, request, user) -> None:
        # Custom User uses email as the login field; keep username = email
        # for admin display and backwards compat.
        if not getattr(user, "username", None):
            user.username = getattr(user, "email", "") or ""


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Social adapter with ORCID link-if-logged-in behaviour."""

    def pre_social_login(self, request, sociallogin) -> None:
        """Link to the already-authenticated user if one exists.

        Runs before allauth decides whether to sign up a new account. If the
        request's user is authenticated and the ORCID account isn't already
        attached to some *other* local user, attach it to the current user.
        """
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return
        # Already connected → allauth will handle it.
        if sociallogin.is_existing:
            return
        sociallogin.connect(request, user)

    def populate_user(self, request, sociallogin, data: dict):
        """Populate ``name`` and ``orcid`` on newly-created users from ORCID."""
        user = super().populate_user(request, sociallogin, data)
        provider = sociallogin.account.provider if sociallogin.account_id else None
        if provider == "orcid":
            extra = sociallogin.account.extra_data or {}
            # ORCID iD is the account UID.
            orcid_id = sociallogin.account.uid
            if orcid_id and not getattr(user, "orcid", None):
                user.orcid = orcid_id
            # Derive a display name from ORCID's person record when available.
            if not getattr(user, "name", None):
                name = _extract_orcid_name(extra)
                if name:
                    user.name = name
        # Mirror email into username for the custom User model.
        if getattr(user, "email", None) and not getattr(user, "username", None):
            user.username = user.email
        return user


def _extract_orcid_name(extra_data: dict) -> str | None:
    """Best-effort name extraction from the ORCID person payload."""
    person = (extra_data or {}).get("person") or {}
    name_obj = person.get("name") or {}
    given = (name_obj.get("given-names") or {}).get("value")
    family = (name_obj.get("family-name") or {}).get("value")
    credit = (name_obj.get("credit-name") or {}).get("value")
    if credit:
        return credit
    parts = [p for p in (given, family) if p]
    return " ".join(parts) if parts else None
