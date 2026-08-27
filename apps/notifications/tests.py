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


@pytest.mark.django_db
def test_badge_endpoint_clears_at_zero(client, user):
    """At zero the partial still renders, so the chrome's count is cleared."""
    client.force_login(user)
    response = client.get(reverse("notifications:badge"))
    assert response.status_code == 200
    body = response.content.decode()
    # Both out-of-band targets are present, both empty.
    assert 'id="notification-badge"' in body
    assert 'id="sidebar-notification-badge"' in body
    assert "bg-brand-coral" not in body


@pytest.mark.django_db
def test_badge_endpoint_updates_both_chrome_badges(client, user):
    create_notification(users=[user], title="x", body="")
    client.force_login(user)
    body = client.get(reverse("notifications:badge")).content.decode()
    assert body.count('hx-swap-oob="innerHTML"') == 2
    assert 'id="notification-badge"' in body
    assert 'id="sidebar-notification-badge"' in body


@pytest.mark.django_db
def test_list_unread_filter(client, user):
    read = create_notification(users=[user], title="Already read", body="")
    read.read = True
    read.save(update_fields=["read"])
    create_notification(users=[user], title="Still unread", body="")

    client.force_login(user)

    all_body = client.get(reverse("notifications:list")).content.decode()
    assert "Already read" in all_body
    assert "Still unread" in all_body

    unread_body = client.get(
        reverse("notifications:list"), {"filter": "unread"}
    ).content.decode()
    assert "Already read" not in unread_body
    assert "Still unread" in unread_body


@pytest.mark.django_db
def test_chrome_shows_notifications_link_and_count(client, user):
    """The sidebar link and top-bar bell are rendered for signed-in users."""
    create_notification(users=[user], title="Ping", body="")
    client.force_login(user)
    body = client.get(reverse("projects:dashboard")).content.decode()

    assert reverse("notifications:list") in body
    assert 'aria-label="Notifications"' in body
    assert 'id="notification-badge"' in body
    assert 'id="sidebar-notification-badge"' in body
    # Unread count painted server-side on first load.
    assert body.count(">1</span>") >= 2


@pytest.mark.django_db
def test_context_processor_counts_only_unread_for_this_user(
    client, user, other_user
):
    create_notification(users=[user], title="Mine", body="")
    create_notification(users=[other_user], title="Theirs", body="")
    client.force_login(user)
    response = client.get(reverse("notifications:list"))
    assert response.context["unread_notifications_count"] == 1


@pytest.mark.django_db
def test_dropdown_syncs_badges(client, user):
    create_notification(users=[user], title="Ping", body="")
    client.force_login(user)
    body = client.get(reverse("notifications:dropdown")).content.decode()
    assert 'id="notification-badge"' in body
    assert 'id="sidebar-notification-badge"' in body
