"""Tests for the audit log.

Covers the append-only invariants (save/delete guards), the ``log_action``
service, the Django auth signal handlers (login / logout / failed login),
and the signup / password-reset / password-change wire-ups in the accounts
views.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import log_action


# ---------------------------------------------------------------------------
# Immutability guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_on_existing_log_raises(user):
    log = log_action(AuditAction.USER_LOGIN, actor=user)
    log.metadata = {"mutated": True}
    with pytest.raises(IntegrityError):
        log.save()


@pytest.mark.django_db
def test_delete_raises(user):
    log = log_action(AuditAction.USER_LOGIN, actor=user)
    with pytest.raises(IntegrityError):
        log.delete()


# ---------------------------------------------------------------------------
# log_action service
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_log_action_records_basic_fields(user):
    log = log_action(
        AuditAction.USER_LOGIN,
        actor=user,
        metadata={"ip": "127.0.0.1"},
    )
    assert log.action == "user.login"
    assert log.actor_id == user.pk
    assert log.metadata == {"ip": "127.0.0.1"}
    assert log.timestamp is not None


@pytest.mark.django_db
def test_log_action_derives_entity_from_instance(user):
    log = log_action(AuditAction.PROFILE_UPDATED, actor=user, entity=user)
    # ``_meta.model_name`` is lowercase Django model name.
    assert log.entity_type == user._meta.model_name
    assert log.entity_id == user.pk


@pytest.mark.django_db
def test_log_action_accepts_string_action(user):
    log = log_action("task.created", actor=user)
    assert log.action == "task.created"


@pytest.mark.django_db
def test_log_action_allows_null_actor():
    log = log_action(AuditAction.USER_LOGIN_FAILED, actor=None)
    assert log.actor is None
    assert log.action == "user.login_failed"


# ---------------------------------------------------------------------------
# Auth signal handlers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_successful_login_writes_audit_row(client, user):
    client.force_login(user)
    # force_login fires the user_logged_in signal.
    logs = AuditLog.objects.filter(action=AuditAction.USER_LOGIN, actor=user)
    assert logs.count() == 1


@pytest.mark.django_db
def test_login_failed_writes_audit_row(client):
    # Hit the login view with bad credentials.
    resp = client.post(
        reverse("accounts:login"),
        {"email": "noone@example.com", "password": "wrong"},
    )
    assert resp.status_code in (200, 302)
    logs = AuditLog.objects.filter(action=AuditAction.USER_LOGIN_FAILED)
    assert logs.count() == 1
    # Password must NEVER appear in metadata.
    for log in logs:
        assert "password" not in (log.metadata or {})
        assert (log.metadata or {}).get("identifier") == "noone@example.com"


@pytest.mark.django_db
def test_logout_writes_audit_row(client, user):
    client.force_login(user)
    client.post(reverse("accounts:logout"))
    logs = AuditLog.objects.filter(action=AuditAction.USER_LOGOUT)
    assert logs.count() == 1


# ---------------------------------------------------------------------------
# Accounts view wire-ups
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_signup_writes_audit_row(client):
    resp = client.post(
        reverse("accounts:signup"),
        {
            "given_name": "New",
            "family_name": "User",
            "email": "newbie@example.com",
            "email_confirm": "newbie@example.com",
            "password": "correct horse staple",
            "password_confirm": "correct horse staple",
            "agree_terms": "on",
        },
    )
    assert resp.status_code in (200, 302)
    assert AuditLog.objects.filter(action=AuditAction.USER_SIGNUP).exists()


@pytest.mark.django_db
def test_password_change_writes_audit_row(client, user):
    user.set_password("oldpassword123")
    user.save()
    client.force_login(user)
    resp = client.post(
        reverse("accounts:password_change"),
        {
            "current_password": "oldpassword123",
            "new_password": "brand-new-password-1",
            "new_password_confirm": "brand-new-password-1",
        },
    )
    assert resp.status_code in (200, 302)
    assert AuditLog.objects.filter(
        action=AuditAction.PASSWORD_CHANGED, actor=user
    ).exists()


@pytest.mark.django_db
def test_password_reset_request_writes_audit_row_when_user_exists(client, user):
    client.post(reverse("accounts:password_reset_request"), {"email": user.email})
    assert AuditLog.objects.filter(
        action=AuditAction.PASSWORD_RESET_REQUESTED, actor=user
    ).exists()


@pytest.mark.django_db
def test_password_reset_request_writes_nothing_for_unknown_email(client):
    client.post(reverse("accounts:password_reset_request"), {"email": "ghost@example.com"})
    assert not AuditLog.objects.filter(action=AuditAction.PASSWORD_RESET_REQUESTED).exists()
