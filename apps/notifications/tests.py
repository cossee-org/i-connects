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
    assert 'data-tooltip="Notifications"' in body
    assert 'id="notification-badge"' in body
    assert 'id="sidebar-notification-badge"' in body
    # Unread count painted server-side on first load.
    assert body.count(">1<") >= 2


@pytest.mark.django_db
def test_topbar_count_sits_beside_the_bell_not_over_it(client, user):
    """The pill is inside the button, after the glyph — never on top of it.

    It is also the button's only visible text, so the button is named by
    sr-only text instead of an aria-label that would hide the count.
    """
    create_notification(users=[user], title="Ping", body="")
    client.force_login(user)
    body = client.get(reverse("projects:dashboard")).content.decode()

    button = body.split('data-tooltip="Notifications"', 1)[1].split("</button>", 1)[0]
    assert "<svg" in button
    assert 'id="notification-badge"' in button
    assert button.index("<svg") < button.index('id="notification-badge"')
    assert 'class="sr-only">Notifications<' in button
    # Nothing pins the badge over the glyph any more.
    assert "position:absolute" not in button
    # The count is announced when the poll changes it (A11Y_GUIDE.md §5).
    assert 'aria-live="polite"' in button


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


# ---------------------------------------------------------------------------
# Click-through routing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_route_resolves_to_the_component_url(user):
    n = create_notification(
        users=[user],
        title="Task completed: Draft",
        body="",
        route="tasks:detail",
        route_kwargs={"project_pk": 7, "pk": 3},
    )
    assert n.route_data == {
        "route": "tasks:detail",
        "kwargs": {"project_pk": 7, "pk": 3},
    }
    assert n.url == "/projects/7/tasks/3/"


@pytest.mark.django_db
def test_open_marks_read_and_redirects_to_the_component(client, user):
    n = create_notification(
        users=[user],
        title="Task completed",
        body="",
        route="tasks:detail",
        route_kwargs={"project_pk": 7, "pk": 3},
    )
    client.force_login(user)
    response = client.get(reverse("notifications:open", kwargs={"pk": n.pk}))
    assert response.status_code == 302
    assert response.url == "/projects/7/tasks/3/"
    n.refresh_from_db()
    assert n.read is True


@pytest.mark.django_db
def test_open_is_scoped_to_the_recipient(client, user, other_user):
    n = create_notification(users=[other_user], title="Theirs", body="")
    client.force_login(user)
    response = client.get(reverse("notifications:open", kwargs={"pk": n.pk}))
    assert response.status_code == 404
    n.refresh_from_db()
    assert n.read is False


@pytest.mark.django_db
def test_unreversible_route_falls_back_instead_of_erroring(user):
    """A route that no longer reverses must not break the list page."""
    from apps.core.enums import ProjectStatus
    from apps.projects.services import create_project_with_owner

    project = create_project_with_owner(
        user=user, form_data={"name": "Fallback", "status": ProjectStatus.ACTIVE}
    )
    n = create_notification(
        users=[user],
        title="Stale",
        body="",
        project=project,
        route="tasks:detail",
        route_kwargs={"project_pk": "nope", "pk": "nope"},
    )
    assert n.url == reverse("projects:detail", kwargs={"pk": project.pk})


@pytest.mark.django_db
def test_notification_without_a_target_is_not_clickable(user):
    n = create_notification(users=[user], title="Nowhere to go", body="")
    assert n.url is None
    assert n.external_link is None


@pytest.mark.django_db
def test_external_announcement_link_is_never_a_redirect_target(user):
    """An admin's off-site link is shown, but never routed through `open`."""
    n = create_notification(
        users=[user], title="Read this", body="", link="https://example.com/x"
    )
    assert n.url is None
    assert n.external_link == "https://example.com/x"


@pytest.mark.django_db
def test_internal_announcement_link_is_used_for_navigation(user):
    n = create_notification(
        users=[user], title="See the board", body="", link="/projects/4/"
    )
    assert n.url == "/projects/4/"
    assert n.external_link is None


@pytest.mark.parametrize(
    "link", ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x", "//evil.example"]
)
def test_announcement_form_rejects_dangerous_links(link):
    from apps.notifications.forms import AnnouncementForm

    form = AnnouncementForm({"title": "t", "body": "b", "link": link})
    assert not form.is_valid()
    assert "link" in form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("link", ["/projects/4/", "https://example.com/x"])
def test_announcement_form_accepts_paths_and_http_links(link):
    from apps.notifications.forms import AnnouncementForm

    form = AnnouncementForm({"title": "t", "body": "b", "link": link})
    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Mark all as read
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_mark_all_read_from_the_list_page_redirects_back(client, user):
    create_notification(users=[user], title="A", body="")
    create_notification(users=[user], title="B", body="")
    client.force_login(user)
    response = client.post(reverse("notifications:mark_all_read"))
    assert response.status_code == 302
    assert response.url == reverse("notifications:list")
    assert Notification.objects.unread_for_user(user).count() == 0


@pytest.mark.django_db
def test_mark_all_read_keeps_the_unread_tab(client, user):
    create_notification(users=[user], title="A", body="")
    client.force_login(user)
    response = client.post(
        reverse("notifications:mark_all_read"), {"next": "?filter=unread"}
    )
    assert response.url == reverse("notifications:list") + "?filter=unread"


@pytest.mark.django_db
def test_mark_all_read_next_cannot_be_pointed_off_site(client, user):
    create_notification(users=[user], title="A", body="")
    client.force_login(user)
    response = client.post(
        reverse("notifications:mark_all_read"), {"next": "https://evil.example"}
    )
    assert response.url == reverse("notifications:list")


@pytest.mark.django_db
def test_mark_all_read_from_the_panel_swaps_in_place(client, user):
    create_notification(users=[user], title="A", body="")
    client.force_login(user)
    response = client.post(
        reverse("notifications:mark_all_read"), HTTP_HX_REQUEST="true"
    )
    assert response.status_code == 200
    assert 'id="notification-badge"' in response.content.decode()


@pytest.mark.django_db
def test_list_page_offers_mark_all_as_read_only_when_unread(client, user):
    client.force_login(user)
    assert "Mark all as read" not in client.get(
        reverse("notifications:list")
    ).content.decode()

    create_notification(users=[user], title="A", body="")
    assert "Mark all as read" in client.get(
        reverse("notifications:list")
    ).content.decode()
