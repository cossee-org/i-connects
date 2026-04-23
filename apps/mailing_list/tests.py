"""Tests for the mailing_list app."""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse

from apps.core.enums import ProjectStatus
from apps.mailing_list.models import MailingListMessage
from apps.mailing_list.services import send_mailing_list_message
from apps.projects.models import ProjectMember
from apps.projects.services import create_project_with_owner


def _project_with_members(pm_user, *members):
    project = create_project_with_owner(
        user=pm_user,
        form_data={
            "name": "P",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )
    for m in members:
        row = ProjectMember.objects.create(project=project)
        row.users.add(m)
    return project


@pytest.mark.django_db
def test_service_sends_email_archives_and_dedupes(pm_user, user, other_user):
    project = _project_with_members(pm_user, user, other_user)
    # Add ``user`` a second time via another ProjectMember row to prove the
    # service dedupes to a single email per user.
    duplicate = ProjectMember.objects.create(project=project)
    duplicate.users.add(user)

    mail.outbox = []
    message = send_mailing_list_message(
        project=project,
        sender=pm_user,
        subject="Hello",
        body="Welcome to the project.",
    )

    assert MailingListMessage.objects.count() == 1
    assert message.recipient_count == 2  # user + other_user; pm excluded
    # Two emails, one each to user and other_user (duplicate membership deduped).
    assert len(mail.outbox) == 2
    recipients = sorted(addr for msg in mail.outbox for addr in msg.to)
    assert recipients == sorted([user.email, other_user.email])
    # Subject is prefixed with the project name.
    assert all(msg.subject == f"[{project.name}] Hello" for msg in mail.outbox)
    # reply_to is set to the sender so recipients can reply back directly.
    assert all(msg.reply_to == [pm_user.email] for msg in mail.outbox)


@pytest.mark.django_db
def test_recipient_count_matches_unique_users(pm_user, user, other_user):
    project = _project_with_members(pm_user, user, other_user)
    team = ProjectMember.objects.create(project=project, name="Team A")
    team.users.add(user, other_user)

    message = send_mailing_list_message(
        project=project, sender=pm_user, subject="S", body="B"
    )
    # user + other_user counted once each; pm_user excluded.
    assert message.recipient_count == 2


@pytest.mark.django_db
def test_compose_requires_project_manager(client, pm_user, user):
    project = _project_with_members(pm_user, user)
    client.force_login(user)
    url = reverse("mailing_list:compose", kwargs={"project_pk": project.pk})
    resp = client.get(url)
    assert resp.status_code == 403
    resp = client.post(url, {"subject": "x", "body": "y"})
    assert resp.status_code == 403
    # Nothing archived, nothing sent.
    assert MailingListMessage.objects.count() == 0


@pytest.mark.django_db
def test_archive_lists_messages_in_reverse_chronological_order(client, pm_user, user):
    project = _project_with_members(pm_user, user)
    first = send_mailing_list_message(
        project=project, sender=pm_user, subject="First", body="a"
    )
    second = send_mailing_list_message(
        project=project, sender=pm_user, subject="Second", body="b"
    )

    client.force_login(user)
    resp = client.get(
        reverse("mailing_list:archive", kwargs={"project_pk": project.pk})
    )
    assert resp.status_code == 200
    ids = [m.pk for m in resp.context["page_obj"].object_list]
    assert ids == [second.pk, first.pk]


@pytest.mark.django_db
def test_detail_page_renders_for_members(client, pm_user, user):
    project = _project_with_members(pm_user, user)
    message = send_mailing_list_message(
        project=project, sender=pm_user, subject="Hi", body="Body"
    )
    client.force_login(user)
    resp = client.get(
        reverse(
            "mailing_list:detail",
            kwargs={"project_pk": project.pk, "pk": message.pk},
        )
    )
    assert resp.status_code == 200
    assert b"Hi" in resp.content
