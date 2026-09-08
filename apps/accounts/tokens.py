"""Token generators for the accounts app.

Django's :class:`~django.contrib.auth.tokens.PasswordResetTokenGenerator`
already produces safe, signed, time-limited tokens that invalidate when the
user's password changes (because the hashed password is part of the token
payload). We subclass it primarily for clarity and so we can hook in extra
state later (e.g. email) without touching call sites.
"""

from __future__ import annotations

from django.contrib.auth.tokens import PasswordResetTokenGenerator


class PasswordResetTokenGen(PasswordResetTokenGenerator):
    """Password-reset token generator.

    Extends the default hash by mixing in the user's email so that changing
    the email after a reset link is issued invalidates the outstanding link.
    """

    def _make_hash_value(self, user, timestamp: int) -> str:  # type: ignore[override]
        base = super()._make_hash_value(user, timestamp)
        return f"{base}:{(user.email or '').lower()}"


password_reset_token = PasswordResetTokenGen()


class EmailConfirmationTokenGen(PasswordResetTokenGenerator):
    """Signup email-confirmation token generator.

    Mixes in the user's email so the link invalidates if the address
    changes before it's used, and in ``email_confirmed_at`` so a token
    stops working the moment it's been consumed once (confirming the
    account changes that value, which changes the hash).
    """

    def _make_hash_value(self, user, timestamp: int) -> str:  # type: ignore[override]
        base = super()._make_hash_value(user, timestamp)
        return f"{base}:{(user.email or '').lower()}:{user.email_confirmed_at}"


email_confirmation_token = EmailConfirmationTokenGen()
