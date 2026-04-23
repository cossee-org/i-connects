"""Tests for the account lockout flow (Ultra Plan §7)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.lockout import (
    FAILED_ATTEMPT_THRESHOLD,
    _hash_token,
    _issue_token,
    count_recent_failures,
    trigger_lockout,
    unlock_with_token,
)
from apps.accounts.models import LockoutToken
from apps.audit.models import AuditAction, AuditLog

User = get_user_model()


def _submit_bad_login(client: Client, email: str) -> None:
    client.post(reverse("accounts:login"), {"email": email, "password": "wrong"})


@pytest.mark.django_db
def test_count_recent_failures_counts_window(user):
    assert count_recent_failures(user.email) == 0
    # 3 failures should count exactly 3.
    for _ in range(3):
        AuditLog.objects.create(
            action=AuditAction.USER_LOGIN_FAILED,
            metadata={"identifier": user.email},
        )
    assert count_recent_failures(user.email) == 3


@pytest.mark.django_db
def test_old_failures_outside_window_do_not_count(user):
    old = AuditLog.objects.create(
        action=AuditAction.USER_LOGIN_FAILED,
        metadata={"identifier": user.email},
    )
    # Backdate past the window.
    AuditLog.objects.filter(pk=old.pk).update(
        timestamp=timezone.now() - timedelta(hours=1)
    )
    assert count_recent_failures(user.email) == 0


@pytest.mark.django_db
def test_nth_failure_triggers_lockout(client, user):
    for _ in range(FAILED_ATTEMPT_THRESHOLD):
        _submit_bad_login(client, user.email)
    user.refresh_from_db()
    assert user.locked_at is not None
    assert LockoutToken.objects.filter(user=user).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOCKED_OUT).exists()
    # Exactly one lockout email sent.
    lockout_mails = [m for m in mail.outbox if "locked" in m.subject.lower()]
    assert len(lockout_mails) == 1
    assert user.email in lockout_mails[0].to


@pytest.mark.django_db
def test_unknown_email_does_not_trigger_anything(client):
    for _ in range(FAILED_ATTEMPT_THRESHOLD + 2):
        _submit_bad_login(client, "ghost@example.com")
    # No lockout row and no lockout email.
    assert not LockoutToken.objects.exists()
    assert not AuditLog.objects.filter(action=AuditAction.ACCOUNT_LOCKED_OUT).exists()
    assert not any("locked" in msg.subject.lower() for msg in mail.outbox)


@pytest.mark.django_db
def test_locked_user_cannot_log_in_with_correct_password(client, user):
    user.set_password("correct-horse-staple")
    user.save()
    trigger_lockout(user)
    resp = client.post(
        reverse("accounts:login"),
        {"email": user.email, "password": "correct-horse-staple"},
    )
    # Still on the login page; not logged in.
    assert "_auth_user_id" not in client.session
    body = resp.content.decode()
    assert "locked" in body.lower()


@pytest.mark.django_db
def test_unlock_with_token_clears_lockout(user):
    trigger_lockout(user)
    token_row = LockoutToken.objects.filter(user=user).order_by("-created_at").first()
    assert token_row is not None
    # Raw tokens aren't stored; re-issue a known one to test the helper.
    fresh_token, raw = _issue_token(user)

    result = unlock_with_token(raw)
    assert result == user
    user.refresh_from_db()
    assert user.locked_at is None
    fresh_token.refresh_from_db()
    assert fresh_token.used_at is not None
    assert AuditLog.objects.filter(action=AuditAction.ACCOUNT_UNLOCKED).exists()


@pytest.mark.django_db
def test_unlock_token_is_one_shot(user):
    trigger_lockout(user)
    _token, raw = _issue_token(user)

    first = unlock_with_token(raw)
    second = unlock_with_token(raw)
    assert first == user
    assert second is None


@pytest.mark.django_db
def test_expired_unlock_token_rejected(user):
    trigger_lockout(user)
    token, raw = _issue_token(user)
    LockoutToken.objects.filter(pk=token.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert unlock_with_token(raw) is None
    # User remains locked.
    user.refresh_from_db()
    assert user.locked_at is not None


@pytest.mark.django_db
def test_unlock_endpoint_accepts_valid_token(client, user):
    trigger_lockout(user)
    _token, raw = _issue_token(user)
    resp = client.get(reverse("accounts:lockout_unlock", kwargs={"token": raw}))
    assert resp.status_code == 200
    assert b"unlocked" in resp.content.lower()
    user.refresh_from_db()
    assert user.locked_at is None


@pytest.mark.django_db
def test_unlock_endpoint_rejects_bogus_token(client):
    resp = client.get(reverse("accounts:lockout_unlock", kwargs={"token": "nope"}))
    assert resp.status_code == 200
    assert b"not valid" in resp.content.lower()


@pytest.mark.django_db
def test_trigger_lockout_is_idempotent(user):
    trigger_lockout(user)
    first_count = LockoutToken.objects.filter(user=user).count()
    trigger_lockout(user)  # already locked — should be a no-op
    assert LockoutToken.objects.filter(user=user).count() == first_count


@pytest.mark.django_db
def test_hash_token_consistent():
    assert _hash_token("abc") == _hash_token("abc")
    assert _hash_token("abc") != _hash_token("abd")
