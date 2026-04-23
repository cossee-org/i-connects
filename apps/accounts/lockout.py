"""Account lockout — Ultra Plan §7.

    Account lockout: after 10 failed login attempts, require email
    confirmation (implement in v1).

Design
------
* The threshold is 10 failed attempts within a rolling 15-minute window,
  counted per-email from ``AuditLog`` rows with
  ``action="user.login_failed"``. The audit log is the source of truth;
  we do not maintain a parallel counter.
* When the threshold is breached we set ``User.locked_at`` and mint a
  ``LockoutToken``. The raw token is emailed; only its SHA-256 hash is
  stored. One-time use, 24-hour expiry.
* While ``locked_at`` is non-null the login form refuses to authenticate
  the user — independent of whether the password is right — and
  instructs them to check their email.
* An attacker probing unknown emails triggers nothing, because
  ``_maybe_trigger_lockout`` only acts on real users.
* Recovery path: clicking the email link calls ``unlock_with_token``,
  which clears ``locked_at`` and marks the token used. The auto-expiry
  is a safety net, not an alternative to email confirmation.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import log_action

from .emails import send_lockout_email
from .models import LockoutToken

User = get_user_model()


FAILED_ATTEMPT_THRESHOLD = 10
FAILED_ATTEMPT_WINDOW = timedelta(minutes=15)
TOKEN_VALIDITY = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def count_recent_failures(email: str) -> int:
    """Return the count of ``user.login_failed`` audit rows for this email
    within the rolling threshold window."""
    if not email:
        return 0
    since = timezone.now() - FAILED_ATTEMPT_WINDOW
    return AuditLog.objects.filter(
        action=AuditAction.USER_LOGIN_FAILED,
        timestamp__gte=since,
        metadata__identifier__iexact=email,
    ).count()


def is_locked(user) -> bool:
    """True if the user is currently locked out."""
    return user is not None and user.locked_at is not None


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue_token(user) -> tuple[LockoutToken, str]:
    raw = secrets.token_urlsafe(32)
    token = LockoutToken.objects.create(
        user=user,
        token_hash=_hash_token(raw),
        expires_at=timezone.now() + TOKEN_VALIDITY,
    )
    return token, raw


# ---------------------------------------------------------------------------
# Trigger & unlock
# ---------------------------------------------------------------------------


@transaction.atomic
def trigger_lockout(user, *, request=None) -> Optional[LockoutToken]:
    """Lock ``user`` and email them a one-time unlock link.

    Idempotent: if the user is already locked we do NOT issue a second
    token (that would let an attacker flood the user's inbox). Call sites
    should check ``is_locked`` first; this function protects itself just
    in case.
    """
    if user.locked_at is not None:
        return None

    user.locked_at = timezone.now()
    user.save(update_fields=["locked_at", "updated_at"])

    token, raw = _issue_token(user)
    log_action(
        AuditAction.ACCOUNT_LOCKED_OUT,
        actor=None,  # locking is a security reaction, not a user action
        entity=user,
        metadata={"threshold": FAILED_ATTEMPT_THRESHOLD},
    )
    # Email is best-effort: if SMTP is down, the lockout still applies and an
    # admin can clear ``locked_at`` manually. We log so the outage is visible
    # in the audit trail.
    try:
        send_lockout_email(user, raw_token=raw, request=request)
    except Exception as exc:  # noqa: BLE001
        log_action(
            AuditAction.ACCOUNT_LOCKED_OUT,
            actor=None,
            entity=user,
            metadata={"email_delivery_error": str(exc)[:200]},
        )
    return token


def _maybe_trigger_lockout(email: str, *, request=None) -> None:
    """Called from the login-failed signal. If a real user for ``email``
    exists and they are over the threshold, lock them out."""
    if not email:
        return
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        return
    if user.locked_at is not None:
        return
    if count_recent_failures(email) < FAILED_ATTEMPT_THRESHOLD:
        return
    trigger_lockout(user, request=request)


@transaction.atomic
def unlock_with_token(raw_token: str) -> Optional[User]:
    """Consume ``raw_token``. Returns the user on success, ``None`` on
    any failure (bad token, expired, already used, user not locked)."""
    if not raw_token:
        return None
    try:
        token = LockoutToken.objects.select_for_update().get(
            token_hash=_hash_token(raw_token)
        )
    except LockoutToken.DoesNotExist:
        return None
    if not token.is_usable:
        return None
    user = token.user
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    if user.locked_at is not None:
        user.locked_at = None
        user.save(update_fields=["locked_at", "updated_at"])
        log_action(
            AuditAction.ACCOUNT_UNLOCKED,
            actor=user,
            entity=user,
        )
    return user


__all__ = [
    "FAILED_ATTEMPT_THRESHOLD",
    "FAILED_ATTEMPT_WINDOW",
    "count_recent_failures",
    "is_locked",
    "trigger_lockout",
    "unlock_with_token",
]
