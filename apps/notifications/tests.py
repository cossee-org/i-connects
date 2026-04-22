"""Smoke tests for the notifications app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.notifications.models import Notification
from apps.notifications.services import create_notification


@pytest.mark.django_db
def test_create_notification_service(user):
    n = create_notification(users=[user], title="Hi", body="Body text")
    assert Notification.objects.filter(pk=n.pk).exists()
    assert list(n.recipients.all()) == [user]
    # Title + body get concatenated into ``message``.
    assert "Hi" in n.message
    assert "Body text" in n.message


@pytest.mark.django_db
def test_for_user_queryset(user, other_user):
    create_notification(users=[user], title="A", body="")
    create_notification(users=[user], title="B", body="")
    create_notification(users=[other_user], title="C", body="")
    assert Notification.objects.for_user(user).count() == 2
    assert Notification.objects.for_user(other_user).count() == 1


@pytest.mark.django_db
def test_mark_read_endpoint(client, user):
    n = create_notification(users=[user], title="Readme", body="")
    assert n.read is False
    client.force_login(user)
    response = client.post(
        reverse("notifications:mark_read", kwargs={"pk": n.pk})
    )
    assert response.status_code == 200
    n.refresh_from_db()
    assert n.read is True


@pytest.mark.django_db
def test_badge_endpoint_returns_count(client, user):
    create_notification(users=[user], title="x", body="")
    client.force_login(user)
    response = client.get(reverse("notifications:badge"))
    assert response.status_code == 200
    # Unread count (1) should appear somewhere in the badge partial.
    assert b"1" in response.content
