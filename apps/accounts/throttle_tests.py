"""Tests for per-IP throttling of signup and password-reset endpoints."""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.throttle import THROTTLE_THRESHOLD
from apps.audit.models import AuditAction, AuditLog

# The throttle is disabled by default in the test settings so unrelated
# suites aren't perturbed by the counter. This module exercises the
# throttle itself, so each test re-enables it explicitly.
enable_throttle = override_settings(THROTTLE_ENABLED=True)


def _signup_payload(n: int) -> dict:
    """Unique valid signup payload keyed by ``n`` (email + name differ)."""
    return {
        "given_name": f"User",
        "family_name": f"{n}",
        "email": f"user{n}@test.com",
        "email_confirm": f"user{n}@test.com",
        "password": "correct-horse-staple",
        "password_confirm": "correct-horse-staple",
        "agree_terms": "on",
    }


@enable_throttle
@pytest.mark.django_db
def test_signup_throttled_after_5_attempts(client):
    url = reverse("accounts:signup")
    # THROTTLE_THRESHOLD successful-shape submissions — each below the limit.
    # A successful signup auto-logs-in; subsequent POSTs would be redirected
    # by ``dispatch`` before reaching the throttle. Log out between attempts
    # so every request actually hits the throttle check.
    for i in range(THROTTLE_THRESHOLD):
        resp = client.post(url, _signup_payload(i))
        assert resp.status_code != 429
        client.logout()
    # The next one tips us over the threshold.
    resp = client.post(url, _signup_payload(THROTTLE_THRESHOLD))
    assert resp.status_code == 429
    body = resp.content.decode().lower()
    assert "too many" in body


@enable_throttle
@pytest.mark.django_db
def test_password_reset_throttled_after_5_attempts(client, user):
    url = reverse("accounts:password_reset_request")
    for _ in range(THROTTLE_THRESHOLD):
        resp = client.post(url, {"email": user.email})
        assert resp.status_code != 429
    resp = client.post(url, {"email": user.email})
    assert resp.status_code == 429
    body = resp.content.decode().lower()
    assert "too many" in body


@enable_throttle
@pytest.mark.django_db
def test_throttle_counts_independently_per_action(client, user):
    signup_url = reverse("accounts:signup")
    reset_url = reverse("accounts:password_reset_request")
    # Fill the signup counter to the threshold. Log out between attempts
    # so auto-login after a successful signup doesn't bypass ``post``.
    for i in range(THROTTLE_THRESHOLD):
        client.post(signup_url, _signup_payload(i))
        client.logout()
    # Password reset should still be allowed — separate action.
    resp = client.post(reset_url, {"email": user.email})
    assert resp.status_code != 429
    # And the two counters are tracked by distinct AuditAction rows.
    assert AuditLog.objects.filter(
        action=AuditAction.SIGNUP_ATTEMPTED
    ).count() == THROTTLE_THRESHOLD
    assert AuditLog.objects.filter(
        action=AuditAction.PASSWORD_RESET_ATTEMPTED
    ).count() == 1
