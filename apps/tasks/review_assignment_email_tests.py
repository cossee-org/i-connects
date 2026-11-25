"""Tests for telling members, in-app and by email, that they were made a task's reviewer."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError

from apps.communications.models import EmailKind, EmailLog
from apps.core.enums import ProjectStatus
from apps.notifications.models import Notification
from apps.projects.models import ProjectMember
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task
from apps.tasks.services import notify_new_reviewers
from apps.tasks.tasks import send_review_assigned_email_task

SUBJECT = 'i-CONNECTS: you were asked to review "Screen abstracts"'


@pytest.fixture(autouse=True)
def _site_url(settings):
    settings.SITE_URL = "https://iconnects.test"


@pytest.fixture
def django_log(caplog):
    """``caplog`` for the "django" logger, which does not propagate to root."""
    logger = logging.getLogger("django")
    logger.addHandler(caplog.handler)
    yield caplog
    logger.removeHandler(caplog.handler)


def _make_project(owner, *, name="Coral reefs"):
    project = create_project_with_owner(
        user=owner,
        form_data={"name": name, "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    project.containers.all().delete()
    column = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    return project, column


def _primary_member(project, user):
    return ProjectMember.objects.filter(project=project, users=user).first()


def _member(project, *users, name=None):
    member = ProjectMember.objects.create(project=project, name=name)
    member.users.add(*users)
    return member


def _task(project, column, *, name="Screen abstracts", **kwargs):
    return Task.objects.create(
        project=project,
        created_by=project.project_members.order_by("id").first(),
        container=column,
        container_task_order=0,
        name=name,
        **kwargs,
    )


def _review_notifications(user):
    return Notification.objects.for_user(user).filter(
        message__startswith="Review requested"
    )


def _form_data(column, *reviewers, name="Screen abstracts"):
    data = {"name": name, "container": column.pk, "contribution_type": "OTHER"}
    if reviewers:
        data.update(requires_review="on", reviewers=[m.pk for m in reviewers])
    return data


def _create(client, project, column, *reviewers, name="Screen abstracts"):
    return client.post(
        reverse("tasks:create", kwargs={"project_pk": project.pk}),
        _form_data(column, *reviewers, name=name),
    )


def _update(client, task, column, *reviewers):
    return client.post(
        reverse("tasks:update", kwargs={"project_pk": task.project_id, "pk": task.pk}),
        _form_data(column, *reviewers, name=task.name),
    )


# ---------------------------------------------------------------------------
# Task form: create and edit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_creating_a_task_tells_its_reviewers_but_not_the_lead(
    client, user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    lead = _primary_member(project, user)
    bob = _member(project, other_user)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        response = _create(client, project, column, lead, bob)
    assert response.status_code in (200, 302)

    task = Task.objects.get(name="Screen abstracts")
    assert [m.to for m in mail.outbox] == [["bob@test.com"]]
    message = mail.outbox[0]
    assert message.subject == SUBJECT
    url = "https://iconnects.test" + reverse(
        "tasks:detail", kwargs={"project_pk": project.pk, "pk": task.pk}
    )
    assert url in message.body
    assert "Coral reefs" in message.body
    assert "Alice asked you to review a task" in message.body
    assert f'href="{url}"' in message.alternatives[0][0]

    assert _review_notifications(other_user).count() == 1
    assert not _review_notifications(user).exists()


@pytest.mark.django_db
def test_the_email_shows_the_task_deadline(
    user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    task = _task(
        project,
        column,
        deadline=timezone.make_aware(datetime(2026, 11, 20, 17, 0)),
        requires_review=True,
    )
    task.reviewers.add(bob)

    with django_capture_on_commit_callbacks(execute=True):
        notify_new_reviewers(task, {bob.pk}, assigned_by=user)
    assert "Nov 20, 2026" in mail.outbox[0].body


@pytest.mark.django_db
def test_editing_a_task_tells_only_reviewers_the_edit_adds(
    client, user, other_user, pm_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    pm = _member(project, pm_user)
    task = _task(project, column, requires_review=True)
    task.reviewers.add(bob)

    client.force_login(user)
    # Saving with the same reviewers tells nobody.
    with django_capture_on_commit_callbacks(execute=True):
        _update(client, task, column, bob)
    assert mail.outbox == []
    assert not Notification.objects.filter(message__startswith="Review requested").exists()

    with django_capture_on_commit_callbacks(execute=True):
        _update(client, task, column, bob, pm)
    assert [m.to for m in mail.outbox] == [["pm@test.com"]]
    assert _review_notifications(pm_user).count() == 1
    assert not _review_notifications(other_user).exists()


@pytest.mark.django_db
def test_unticking_require_review_tells_nobody(
    client, user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    task = _task(project, column, requires_review=True)
    task.reviewers.add(bob)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        _update(client, task, column)
    assert not task.reviewers.exists()
    assert mail.outbox == []


@pytest.mark.django_db
def test_a_team_reviewer_tells_each_of_its_users(
    client, user, other_user, pm_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    team = _member(project, other_user, pm_user, name="Extraction team")

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        _create(client, project, column, team)
    assert sorted(m.to[0] for m in mail.outbox) == ["bob@test.com", "pm@test.com"]
    notification = Notification.objects.get(message__startswith="Review requested")
    assert set(notification.recipients.values_list("pk", flat=True)) == {
        other_user.pk,
        pm_user.pk,
    }


@pytest.mark.django_db
def test_an_assignee_who_also_reviews_hears_about_both(
    client, user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    bob = _member(project, other_user)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("tasks:create", kwargs={"project_pk": project.pk}),
            {**_form_data(column, bob), "assigned_members": [bob.pk]},
        )
    assert sorted(m.subject for m in mail.outbox) == [
        'i-CONNECTS: you were asked to review "Screen abstracts"',
        'i-CONNECTS: you were assigned "Screen abstracts"',
    ]


@pytest.mark.django_db
def test_opting_out_stops_the_email_but_not_the_bell(
    client, user, other_user, django_capture_on_commit_callbacks
):
    other_user.email_review_assigned = False
    other_user.save(update_fields=["email_review_assigned"])
    project, column = _make_project(user)
    bob = _member(project, other_user)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        _create(client, project, column, bob)
    assert mail.outbox == []
    assert _review_notifications(other_user).count() == 1


@pytest.mark.django_db
def test_turning_off_the_assignment_email_does_not_stop_the_review_email(
    client, user, other_user, django_capture_on_commit_callbacks
):
    other_user.email_task_assigned = False
    other_user.save(update_fields=["email_task_assigned"])
    project, column = _make_project(user)
    bob = _member(project, other_user)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        _create(client, project, column, bob)
    assert [m.subject for m in mail.outbox] == [SUBJECT]


# ---------------------------------------------------------------------------
# A broker outage
# ---------------------------------------------------------------------------


def _broker_down(monkeypatch):
    def boom(*args, **kwargs):
        raise OperationalError("broker unreachable")

    monkeypatch.setattr(send_review_assigned_email_task, "delay", boom)


@pytest.mark.django_db
def test_a_broker_outage_after_commit_does_not_fail_the_request(
    client, user, other_user, django_capture_on_commit_callbacks, monkeypatch, django_log
):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    _broker_down(monkeypatch)

    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        response = _create(client, project, column, bob)

    assert response.status_code in (200, 302)
    assert Task.objects.filter(name="Screen abstracts", reviewers=bob).exists()
    assert _review_notifications(other_user).count() == 1
    assert mail.outbox == []
    assert "broker unreachable" in django_log.text


# ---------------------------------------------------------------------------
# The email task itself
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_no_email_for_a_reviewer_taken_off_before_it_was_sent(
    user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    task = _task(project, column, requires_review=True)
    task.reviewers.add(bob)

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        notify_new_reviewers(task, {bob.pk}, assigned_by=user)
    task.reviewers.remove(bob)
    for callback in callbacks:
        callback()
    assert mail.outbox == []


@pytest.mark.django_db
def test_no_email_for_a_task_deleted_before_it_was_sent(user, other_user):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    task = _task(project, column, requires_review=True)
    task.reviewers.add(bob)
    task_id = task.pk
    task.delete()

    send_review_assigned_email_task(other_user.pk, task_id, user.pk)
    assert mail.outbox == []


@pytest.mark.django_db
def test_the_email_is_logged_as_a_review_assignment(user, other_user):
    project, column = _make_project(user)
    bob = _member(project, other_user)
    task = _task(project, column, requires_review=True)
    task.reviewers.add(bob)

    send_review_assigned_email_task(other_user.pk, task.pk, user.pk)
    row = EmailLog.objects.get()
    assert row.kind == EmailKind.REVIEW_ASSIGNED
    assert row.project == project
    assert row.subject == SUBJECT


@pytest.mark.django_db
def test_task_names_are_not_html_escaped_in_the_plain_text(
    user, other_user, django_capture_on_commit_callbacks
):
    project, column = _make_project(user, name="Fish & frogs")
    bob = _member(project, other_user)
    task = _task(project, column, name="Q&A <draft>", requires_review=True)
    task.reviewers.add(bob)

    with django_capture_on_commit_callbacks(execute=True):
        notify_new_reviewers(task, {bob.pk}, assigned_by=user)
    message = mail.outbox[0]
    assert message.subject == 'i-CONNECTS: you were asked to review "Q&A <draft>"'
    assert "Q&A <draft> (Fish & frogs)" in message.body
    assert "Q&amp;A &lt;draft&gt;" in message.alternatives[0][0]


# ---------------------------------------------------------------------------
# The preference
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_review_email_is_on_by_default():
    user = get_user_model().objects.create_user(
        email="new@test.com", password="password123", name="New"
    )
    assert user.email_review_assigned is True


@pytest.mark.django_db
def test_profile_form_turns_the_review_email_off(client_logged_in, user):
    url = reverse("accounts:profile")
    page = client_logged_in.get(url).content.decode()
    assert "Email me when I&#x27;m asked to review a task" in page

    response = client_logged_in.post(
        url,
        {
            "family_name": "Smith",
            "email_project_activity_frequency": "NEVER",
            "email_overdue_task_frequency": "NEVER",
            "email_task_assigned": "on",
            # Unticked checkboxes are simply absent from the POST.
        },
    )
    assert response.status_code in (200, 302)
    user.refresh_from_db()
    assert user.email_review_assigned is False
    assert user.email_task_assigned is True
