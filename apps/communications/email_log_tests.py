"""Tests for the email log: what gets recorded, and who can read it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils import timezone

from apps.communications.models import EmailKind, EmailLog, EmailStatus
from apps.communications.services import last_sent_by_user, send_logged
from apps.core.emails import send_email
from apps.core.enums import MemberPrivileges, ProjectStatus
from apps.mailing_list.services import send_mailing_list_message
from apps.notifications.services import create_notification
from apps.notifications.tasks import broadcast_announcement_task
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import (
    approve_join_request,
    create_invitation,
    create_join_request,
    resend_invitation,
    create_project_with_owner,
)
from apps.tasks.models import KanbanBoard, Task
from apps.tasks.tasks import send_task_assigned_email_task


@pytest.fixture(autouse=True)
def _site_url(settings):
    settings.SITE_URL = "https://iconnects.test"


@pytest.fixture
def project(pm_user):
    return create_project_with_owner(
        user=pm_user,
        form_data={
            "name": "Coral reefs",
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
        },
    )


def _add_member(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return member


def _log(project, user, *, kind=EmailKind.TASK_ASSIGNED, subject="A subject", **kwargs):
    return EmailLog.objects.create(
        project=project,
        recipient=user,
        to_email=user.email,
        kind=kind,
        subject=subject,
        **kwargs,
    )


def _log_url(project, **params):
    url = reverse("communications:email_log", kwargs={"project_pk": project.pk})
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return url


def _message(*to):
    return EmailMultiAlternatives(
        subject="Hello", body="Body", from_email="noreply@test.com", to=list(to)
    )


# ---------------------------------------------------------------------------
# send_logged
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_send_logged_sends_and_records_one_row_per_recipient(project, user):
    sent = send_logged(
        _message(user.email, "nobody@elsewhere.org"),
        kind=EmailKind.ANNOUNCEMENT,
        project=project,
    )

    assert sent == 1
    assert len(mail.outbox) == 1
    rows = {row.to_email: row for row in EmailLog.objects.all()}
    assert set(rows) == {user.email, "nobody@elsewhere.org"}
    assert rows[user.email].recipient == user
    # An address with no account is still logged, just not linked to one.
    assert rows["nobody@elsewhere.org"].recipient is None
    for row in rows.values():
        assert row.project == project
        assert row.kind == EmailKind.ANNOUNCEMENT
        assert row.subject == "Hello"
        assert row.status == EmailStatus.SENT
        assert row.error == ""


@pytest.mark.django_db
def test_send_logged_matches_the_account_whatever_the_address_case(project, user):
    send_logged(_message(user.email.upper()), kind=EmailKind.DIGEST)

    assert EmailLog.objects.get().recipient == user


@pytest.mark.django_db
def test_a_failed_send_is_logged_and_raised(project, user, monkeypatch):
    def boom(self, fail_silently=False):
        raise ConnectionRefusedError("mail server down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", boom)

    with pytest.raises(ConnectionRefusedError):
        send_logged(_message(user.email), kind=EmailKind.INVITATION, project=project)

    row = EmailLog.objects.get()
    assert row.status == EmailStatus.FAILED
    assert "mail server down" in row.error


@pytest.mark.django_db
def test_a_failed_send_can_be_swallowed_but_is_still_logged(
    project, user, monkeypatch
):
    def boom(self, fail_silently=False):
        raise ConnectionRefusedError("mail server down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", boom)

    assert (
        send_logged(
            _message(user.email),
            kind=EmailKind.INVITATION,
            project=project,
            fail_silently=True,
        )
        == 0
    )
    assert EmailLog.objects.get().status == EmailStatus.FAILED


@pytest.mark.django_db
def test_a_log_that_cannot_be_written_does_not_stop_the_email(
    project, user, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("no table")

    monkeypatch.setattr(EmailLog.objects, "bulk_create", boom)

    assert send_logged(_message(user.email), kind=EmailKind.DIGEST) == 1
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_an_email_sent_without_a_kind_is_not_logged(user):
    """Account emails (welcome, password reset) stay out of the log."""
    send_email("welcome", {"user": user, "site_url": "/"}, [user.email])

    assert len(mail.outbox) == 1
    assert not EmailLog.objects.exists()


# ---------------------------------------------------------------------------
# Each project email is recorded
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_invitation_is_logged_to_an_address_without_an_account(project, pm_user):
    create_invitation(
        project=project,
        email="newcomer@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by=pm_user.name,
        inviter=pm_user,
    )

    row = EmailLog.objects.get()
    assert row.kind == EmailKind.INVITATION
    assert row.to_email == "newcomer@test.com"
    assert row.recipient is None
    assert row.project == project
    assert row.subject == mail.outbox[-1].subject
    assert row.triggered_by == pm_user


@pytest.mark.django_db
def test_a_resent_invitation_is_triggered_by_whoever_resent_it(
    project, pm_user, other_user
):
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.PROJECT_MANAGER
    )
    invitation = create_invitation(
        project=project,
        email="newcomer@test.com",
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by=pm_user.name,
        inviter=pm_user,
    )

    resend_invitation(invitation=invitation, resender=other_user)

    assert [row.triggered_by for row in EmailLog.objects.order_by("id")] == [
        pm_user,
        other_user,
    ]


@pytest.mark.django_db
def test_a_join_request_is_logged_for_each_lead_and_its_decision_for_the_requester(
    project, pm_user, user, other_user
):
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.PROJECT_MANAGER
    )
    mail.outbox = []

    join_request = create_join_request(
        project=project, user=user, requested_privilege=MemberPrivileges.CONTRIBUTOR
    )

    requests = EmailLog.objects.filter(kind=EmailKind.JOIN_REQUEST)
    assert {row.recipient for row in requests} == {pm_user, other_user}
    # Set off by the person asking to join.
    assert {row.triggered_by for row in requests} == {user}
    # Each Lead gets a message of their own, not one with every Lead on it.
    assert sorted(m.to for m in mail.outbox) == sorted(
        [[pm_user.email], [other_user.email]]
    )

    approve_join_request(join_request=join_request, approver=pm_user)

    decision = EmailLog.objects.get(kind=EmailKind.JOIN_REQUEST_DECISION)
    assert decision.recipient == user
    assert decision.project == project
    # Set off by the Lead who decided.
    assert decision.triggered_by == pm_user


@pytest.mark.django_db
def test_a_task_assignment_email_is_logged_with_its_project(project, pm_user, user):
    member = _add_member(project, user)
    column = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    task = Task.objects.create(
        project=project,
        created_by=project.project_members.order_by("id").first(),
        container=column,
        container_task_order=0,
        name="Screen abstracts",
    )
    task.assigned_members.add(member)

    send_task_assigned_email_task(user.pk, [task.pk], pm_user.pk)

    row = EmailLog.objects.get()
    assert row.kind == EmailKind.TASK_ASSIGNED
    assert row.recipient == user
    assert row.project == project
    assert row.triggered_by == pm_user


@pytest.mark.django_db
def test_a_task_assignment_email_that_is_not_sent_is_not_logged(
    project, pm_user, user
):
    """The task returns early when the preference is off: nothing went out."""
    user.email_task_assigned = False
    user.save(update_fields=["email_task_assigned"])

    send_task_assigned_email_task(user.pk, [], pm_user.pk)

    assert not EmailLog.objects.exists()


@pytest.mark.django_db
def test_a_mailing_list_message_is_logged_for_each_recipient(
    project, pm_user, user, other_user
):
    _add_member(project, user)
    _add_member(project, other_user)

    send_mailing_list_message(
        project=project, sender=pm_user, subject="Meeting", body="On Friday."
    )

    rows = EmailLog.objects.filter(kind=EmailKind.MAILING_LIST)
    # Everyone but the sender, who does not get a copy.
    assert {row.recipient for row in rows} == {user, other_user}
    assert {row.subject for row in rows} == {"[Coral reefs] Meeting"}
    assert {row.triggered_by for row in rows} == {pm_user}


@pytest.mark.django_db
def test_an_announcement_is_logged_for_each_recipient(project, user, other_user):
    announcement = create_notification(
        [user, other_user], title="News", project=project, announcement=True
    )

    broadcast_announcement_task(announcement.pk)

    rows = EmailLog.objects.filter(kind=EmailKind.ANNOUNCEMENT, project=project)
    assert {row.recipient for row in rows} == {user, other_user}
    assert {row.triggered_by for row in rows} == {None}


@pytest.mark.django_db
def test_an_announcement_records_the_admin_who_sent_it(project, pm_user, user):
    announcement = create_notification(
        [user], title="News", project=project, announcement=True
    )

    broadcast_announcement_task(announcement.pk, sent_by_id=pm_user.pk)

    assert EmailLog.objects.get().triggered_by == pm_user


@pytest.mark.django_db
def test_a_digest_is_triggered_by_no_one(user):
    send_email(
        "comment_mention",
        {"user": user, "task": type("T", (), {"name": "x"})(), "project": None},
        [user.email],
        kind=EmailKind.DIGEST,
    )

    row = EmailLog.objects.get()
    assert row.triggered_by is None
    assert row.is_automatic


# ---------------------------------------------------------------------------
# The email log page
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_lead_sees_the_projects_emails_newest_first(client, project, pm_user, user):
    _add_member(project, user)
    older = _log(project, user, subject="The older one")
    EmailLog.objects.filter(pk=older.pk).update(
        sent_at=timezone.now() - timedelta(days=3)
    )
    _log(project, user, subject="The newer one", kind=EmailKind.MAILING_LIST)
    client.force_login(pm_user)

    response = client.get(_log_url(project))

    assert response.status_code == 200
    content = response.content.decode()
    assert content.index("The newer one") < content.index("The older one")
    assert user.email in content
    assert "Mailing list message" in content
    assert "Task assigned" in content


@pytest.mark.django_db
def test_a_member_cannot_open_the_email_log(client, project, user):
    _add_member(project, user)
    _log(project, user, subject="Not for members")
    client.force_login(user)

    response = client.get(_log_url(project))

    assert response.status_code == 403
    assert "Not for members" not in response.content.decode()


@pytest.mark.django_db
def test_an_outsider_and_an_anonymous_visitor_cannot_open_the_email_log(
    client, project, other_user
):
    assert client.get(_log_url(project)).status_code in (302, 403)

    client.force_login(other_user)
    assert client.get(_log_url(project)).status_code == 403


@pytest.mark.django_db
def test_the_log_leaves_out_other_projects_and_emails_about_no_project(
    client, project, pm_user, user
):
    other_project = create_project_with_owner(
        user=pm_user,
        form_data={"name": "Kelp", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _log(project, user, subject="About coral")
    _log(other_project, user, subject="About kelp")
    _log(None, user, subject="Your daily digest", kind=EmailKind.DIGEST)
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert "About coral" in content
    assert "About kelp" not in content
    assert "Your daily digest" not in content


@pytest.mark.django_db
def test_the_log_narrows_to_one_member_and_one_kind(
    client, project, pm_user, user, other_user
):
    _add_member(project, user)
    _add_member(project, other_user)
    _log(project, user, subject="Task for Alice")
    _log(project, user, subject="List for Alice", kind=EmailKind.MAILING_LIST)
    _log(project, other_user, subject="Task for Bob")
    client.force_login(pm_user)

    content = client.get(_log_url(project, member=user.pk)).content.decode()
    assert "Task for Alice" in content
    assert "List for Alice" in content
    assert "Task for Bob" not in content

    content = client.get(
        _log_url(project, member=user.pk, kind=EmailKind.MAILING_LIST)
    ).content.decode()
    assert "List for Alice" in content
    assert "Task for Alice" not in content


@pytest.mark.django_db
def test_a_members_log_includes_the_invitation_sent_before_they_had_an_account(
    client, project, pm_user, user
):
    _add_member(project, user)
    EmailLog.objects.create(
        project=project,
        to_email=user.email.upper(),
        kind=EmailKind.INVITATION,
        subject="You are invited",
    )
    client.force_login(pm_user)

    content = client.get(_log_url(project, member=user.pk)).content.decode()

    assert "You are invited" in content


@pytest.mark.django_db
def test_a_filter_that_matches_nothing_is_ignored(client, project, pm_user, user):
    _log(project, user, subject="Still here")
    client.force_login(pm_user)

    response = client.get(_log_url(project, member="abc", kind="nonsense"))

    assert response.status_code == 200
    assert "Still here" in response.content.decode()


@pytest.mark.django_db
def test_a_failed_email_is_marked_failed(client, project, pm_user, user):
    _log(project, user, status=EmailStatus.FAILED, error="SMTP down")
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert "Failed" in content


@pytest.mark.django_db
def test_the_log_is_paginated_and_keeps_its_filters_across_pages(
    client, project, pm_user, user
):
    _add_member(project, user)
    EmailLog.objects.bulk_create(
        EmailLog(
            project=project,
            recipient=user,
            to_email=user.email,
            kind=EmailKind.TASK_ASSIGNED,
            subject=f"Email {n}",
        )
        for n in range(51)
    )
    client.force_login(pm_user)

    response = client.get(_log_url(project, member=user.pk))

    assert response.context["is_paginated"]
    assert len(response.context["emails"]) == 50
    assert f"?member={user.pk}&amp;page=2" in response.content.decode()


def _bulk_log(project, user, count, **kwargs):
    EmailLog.objects.bulk_create(
        EmailLog(
            project=project,
            recipient=user,
            to_email=user.email,
            kind=EmailKind.TASK_ASSIGNED,
            subject=f"Email {n}",
            **kwargs,
        )
        for n in range(count)
    )


@pytest.mark.django_db
def test_the_log_counts_what_it_shows(client, project, pm_user, user):
    _bulk_log(project, user, 60)
    client.force_login(pm_user)

    first = client.get(_log_url(project)).content.decode()
    last = client.get(_log_url(project, page=2)).content.decode()

    assert "Showing 1&ndash;50 of 60 emails" in first
    assert "Showing 51&ndash;60 of 60 emails" in last
    assert 'aria-label="Last page"' in first
    assert 'aria-label="First page"' in last


@pytest.mark.django_db
def test_a_lead_picks_how_many_rows_a_page(client, project, pm_user, user):
    _bulk_log(project, user, 60)
    client.force_login(pm_user)

    response = client.get(_log_url(project, per_page=25))

    assert len(response.context["emails"]) == 25
    assert response.context["paginator"].num_pages == 3
    assert "per_page=25&amp;page=2" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("per_page", ["7", "1000", "abc"])
def test_a_page_size_not_on_offer_falls_back_to_fifty(
    client, project, pm_user, user, per_page
):
    _bulk_log(project, user, 60)
    client.force_login(pm_user)

    response = client.get(_log_url(project, per_page=per_page))

    assert len(response.context["emails"]) == 50


@pytest.mark.django_db
@pytest.mark.parametrize("page, expected", [("99", 2), ("abc", 1), ("0", 1)])
def test_a_page_that_does_not_exist_opens_the_nearest_one(
    client, project, pm_user, user, page, expected
):
    """A stale link, say page 3 of a search since narrowed, is not a 404."""
    _bulk_log(project, user, 60)
    client.force_login(pm_user)

    response = client.get(_log_url(project, page=page))

    assert response.status_code == 200
    assert response.context["page_obj"].number == expected


@pytest.mark.django_db
@pytest.mark.parametrize(
    "q",
    ["Coral survey", "coral SURVEY", "bea@", "Lund", "transect counts"],
    ids=["subject", "case", "address", "name", "body"],
)
def test_search_finds_an_email_by_subject_address_name_or_text(
    client, project, pm_user, q
):
    from django.contrib.auth import get_user_model

    bea = get_user_model().objects.create_user(
        email="bea@reef.test", password="password123", name="Bea Lund",
        given_name="Bea", family_name="Lund",
    )
    _add_member(project, bea)
    _log(project, bea, subject="Coral survey",
         text_body="Please check the transect counts.")
    _log(project, pm_user, subject="Something else", text_body="Unrelated")
    client.force_login(pm_user)

    response = client.get(_log_url(project, q=q.replace(" ", "+")))

    subjects = [e.subject for e in response.context["emails"]]
    assert subjects == ["Coral survey"]


@pytest.mark.django_db
def test_search_combines_with_the_filters_and_survives_paging(
    client, project, pm_user, user
):
    _add_member(project, user)
    _bulk_log(project, user, 30, text_body="needle")
    _bulk_log(project, user, 30, text_body="haystack")
    _bulk_log(project, pm_user, 30, text_body="needle")
    client.force_login(pm_user)

    response = client.get(_log_url(project, q="needle", member=user.pk, per_page=25))

    assert response.context["paginator"].count == 30
    content = response.content.decode()
    assert f"q=needle&amp;member={user.pk}&amp;per_page=25&amp;page=2" in content
    assert 'value="needle"' in content
    assert "Clear filter" in content


@pytest.mark.django_db
def test_a_search_with_no_match_says_so(client, project, pm_user, user):
    _log(project, user, subject="Coral survey")
    client.force_login(pm_user)

    content = client.get(_log_url(project, q="kelp")).content.decode()

    assert "No emails match" in content
    assert "Coral survey" not in content


@pytest.mark.django_db
def test_search_does_not_reach_other_projects(client, project, pm_user, other_user):
    elsewhere = create_project_with_owner(
        user=other_user,
        form_data={"name": "Kelp", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    _log(elsewhere, other_user, subject="Kelp business")
    client.force_login(pm_user)

    response = client.get(_log_url(project, q="Kelp"))

    assert list(response.context["emails"]) == []


# ---------------------------------------------------------------------------
# Contributors tab
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_last_sent_by_user_takes_the_newest_sent_email(project, user, other_user):
    old = _log(project, user)
    EmailLog.objects.filter(pk=old.pk).update(
        sent_at=timezone.now() - timedelta(days=5)
    )
    newest = _log(project, user)
    # A failed send did not reach anyone, so it is not their last email.
    _log(project, user, status=EmailStatus.FAILED)

    result = last_sent_by_user(project, [user, other_user, None])

    assert result == {user.pk: newest.sent_at}


@pytest.mark.django_db
def test_a_lead_sees_each_members_last_email_linking_to_their_log(
    client, project, pm_user, user
):
    _add_member(project, user)
    _log(project, user)
    client.force_login(pm_user)

    content = client.get(
        reverse("projects:members", kwargs={"pk": project.pk})
    ).content.decode()

    assert "Last email" in content
    assert _log_url(project) in content
    assert f"{_log_url(project)}?member={user.pk}" in content


@pytest.mark.django_db
def test_a_member_sees_neither_the_last_email_column_nor_the_log_link(
    client, project, user
):
    _add_member(project, user)
    _log(project, user)
    client.force_login(user)

    content = client.get(
        reverse("projects:members", kwargs={"pk": project.pk})
    ).content.decode()

    assert "Last email" not in content
    assert _log_url(project) not in content


@pytest.mark.django_db
def test_the_kind_filter_does_not_offer_digests_announcements_or_mailing_lists(
    client, project, pm_user
):
    client.force_login(pm_user)

    response = client.get(_log_url(project))

    kinds = [value for value, _ in response.context["kind_options"]]
    assert EmailKind.DIGEST not in kinds
    assert EmailKind.ANNOUNCEMENT not in kinds
    assert EmailKind.MAILING_LIST not in kinds
    assert EmailKind.INVITATION in kinds
    content = response.content.decode()
    assert "label: 'Announcement'" not in content
    assert "label: 'Mailing list message'" not in content


@pytest.mark.django_db
def test_announcements_and_mailing_list_messages_are_still_listed(
    client, project, pm_user, user
):
    _log(project, user, kind=EmailKind.ANNOUNCEMENT, subject="Site news")
    _log(project, user, kind=EmailKind.MAILING_LIST, subject="Team meeting")
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert "Site news" in content
    assert "Team meeting" in content


# ---------------------------------------------------------------------------
# The email itself
# ---------------------------------------------------------------------------
def _detail_url(project, email):
    return reverse(
        "communications:email_detail",
        kwargs={"project_pk": project.pk, "pk": email.pk},
    )


@pytest.mark.django_db
def test_send_logged_keeps_the_email_for_a_project(project, user):
    message = EmailMultiAlternatives(
        subject="Hello",
        body="Plain body",
        from_email="noreply@test.com",
        to=[user.email],
        reply_to=["lead@test.com"],
    )
    message.attach_alternative("<p>HTML body</p>", "text/html")

    send_logged(message, kind=EmailKind.MAILING_LIST, project=project)

    row = EmailLog.objects.get()
    assert row.text_body == "Plain body"
    assert row.html_body == "<p>HTML body</p>"
    assert row.from_email == "noreply@test.com"
    assert row.reply_to == "lead@test.com"


@pytest.mark.django_db
def test_a_failed_send_keeps_the_email_too(project, user, monkeypatch):
    def boom(self, fail_silently=False):
        raise ConnectionError("SMTP down")

    monkeypatch.setattr(EmailMultiAlternatives, "send", boom)
    send_logged(_message(user.email), kind=EmailKind.TASK_ASSIGNED, project=project, fail_silently=True)

    row = EmailLog.objects.get()
    assert row.status == EmailStatus.FAILED
    assert row.text_body == "Body"


@pytest.mark.django_db
def test_an_email_about_no_project_keeps_no_body(user):
    """No Lead can see it, so nobody should be able to read it."""
    send_logged(_message(user.email), kind=EmailKind.DIGEST)

    row = EmailLog.objects.get()
    assert row.text_body == ""
    assert row.html_body == ""
    assert not row.has_body


@pytest.mark.django_db
def test_a_templated_project_email_keeps_both_bodies(project, user):
    send_email(
        "comment_mention",
        {
            "user": user,
            "author_name": "Asha",
            "task": type("T", (), {"name": "Count corals"})(),
            "project": project,
            "excerpt": "Have a look",
            "url": "https://iconnects.test/t/1/",
        },
        [user.email],
        kind=EmailKind.COMMENT_MENTION,
        project=project,
    )

    row = EmailLog.objects.get()
    assert "Have a look" in row.text_body
    assert "<html" in row.html_body.lower()


@pytest.mark.django_db
def test_each_row_has_a_view_email_button(client, project, pm_user, user):
    _add_member(project, user)
    email = _log(project, user, text_body="Hi")
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert _detail_url(project, email) in content
    assert "View email" in content
    assert 'id="email-modal"' in content


@pytest.mark.django_db
def test_a_lead_opens_the_email_in_a_modal_with_its_metadata(
    client, project, pm_user, user
):
    _add_member(project, user)
    email = _log(
        project,
        user,
        subject="Coral survey",
        kind=EmailKind.MAILING_LIST,
        from_email="noreply@test.com",
        reply_to="lead@test.com",
        text_body="See you at the reef.",
        html_body="<!DOCTYPE html><html><head><title>x</title></head>"
        '<body><p>See you at the <a href="https://reef.test">reef</a>.</p>'
        "<script>alert(1)</script></body></html>",
    )
    client.force_login(pm_user)

    response = client.get(_detail_url(project, email), HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'class="modal-box' in content
    assert "<html" not in content.lower()  # a fragment, not a page
    # The app's corner close button, as on its other modals.
    assert 'class="btn btn-sm btn-circle btn-ghost absolute right-2 top-2" aria-label="Close">&times;</button>' in content
    for text in ("Coral survey", user.email, "noreply@test.com", "lead@test.com",
                 "Mailing list message", "See you at the reef."):
        assert text in content
    # The HTML body is in a sandboxed frame without scripts, escaped into
    # srcdoc, with links sent to a new tab.
    assert 'sandbox="allow-popups allow-popups-to-escape-sandbox"' in content
    assert "allow-scripts" not in content
    assert "allow-same-origin" not in content
    assert "<script>alert(1)</script>" not in content
    assert "&lt;head&gt;&lt;base target=&quot;_blank&quot;&gt;&lt;title&gt;" in content


@pytest.mark.django_db
def test_without_scripting_the_email_opens_as_a_page(client, project, pm_user, user):
    _add_member(project, user)
    email = _log(project, user, subject="Coral survey", text_body="Plain only")
    client.force_login(pm_user)

    content = client.get(_detail_url(project, email)).content.decode()

    assert "<html" in content.lower()
    assert "Email log" in content
    assert "Plain only" in content
    assert "<iframe" not in content


@pytest.mark.django_db
def test_an_email_logged_before_bodies_were_kept_says_so(
    client, project, pm_user, user
):
    _add_member(project, user)
    email = _log(project, user)
    client.force_login(pm_user)

    content = client.get(_detail_url(project, email), HTTP_HX_REQUEST="true").content.decode()

    assert "The content of this email was not kept" in content


@pytest.mark.django_db
def test_only_a_lead_can_read_an_email(client, project, pm_user, user, other_user):
    _add_member(project, user)
    email = _log(project, user, text_body="For Leads only")
    url = _detail_url(project, email)

    # Not even its own recipient, through the log.
    client.force_login(user)
    response = client.get(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 403
    assert "For Leads only" not in response.content.decode()

    client.force_login(other_user)
    assert client.get(url).status_code == 403

    client.logout()
    assert client.get(url).status_code in (302, 403)


@pytest.mark.django_db
def test_a_lead_cannot_read_another_projects_email_through_their_own(
    client, project, pm_user, other_user
):
    elsewhere = create_project_with_owner(
        user=other_user,
        form_data={"name": "Kelp", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    email = _log(elsewhere, other_user, text_body="Kelp business")
    client.force_login(pm_user)

    response = client.get(_detail_url(project, email), HTTP_HX_REQUEST="true")

    assert response.status_code == 404


@pytest.mark.django_db
def test_the_admin_does_not_show_the_email(admin_client, project, user):
    email = _log(project, user, text_body="Secret plans", html_body="<p>Secret plans</p>")

    response = admin_client.get(
        reverse("admin:communications_emaillog_change", args=[email.pk])
    )

    assert response.status_code == 200
    assert "Secret plans" not in response.content.decode()


# ---------------------------------------------------------------------------
# The search and filter bar
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_bar_is_the_apps_search_and_filter_bar(client, project, pm_user, user):
    _add_member(project, user)
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert 'id="filter-form-emails"' in content
    assert 'placeholder="Search by name, address, subject or text…"' in content
    assert 'hx-trigger="input changed delay:300ms"' in content
    assert 'hx-target="#email-results"' in content
    for label in ("Member", "Email", "Per page"):
        assert f"<span>{label}</span>" in content
    assert 'id="email-log-total-badge"' in content
    # Each member is an option of the Member dropdown.
    assert f"value: '{user.pk}'" in content


@pytest.mark.django_db
def test_a_live_search_returns_the_results_alone_with_the_count(
    client, project, pm_user, user
):
    _log(project, user, subject="Coral survey")
    _log(project, user, subject="Kelp survey")
    client.force_login(pm_user)

    response = client.get(_log_url(project, q="coral"), HTTP_HX_REQUEST="true")

    content = response.content.decode()
    assert "<html" not in content.lower()
    assert 'id="filter-form-emails"' not in content
    assert "Coral survey" in content
    assert "Kelp survey" not in content
    assert 'id="email-log-total-badge" hx-swap-oob="true"' in content


@pytest.mark.django_db
def test_the_filters_are_summed_up_in_a_sentence(client, project, pm_user, user):
    _add_member(project, user)
    _log(project, user, subject="Coral survey", kind=EmailKind.MAILING_LIST)
    client.force_login(pm_user)

    plain = client.get(_log_url(project))
    filtered = client.get(
        _log_url(project, member=user.pk, kind=EmailKind.MAILING_LIST, q="coral")
    )

    assert plain.context["results_summary"] is None
    name = user.name or user.email
    assert filtered.context["results_summary"] == (
        f"1 email to {name} of type Mailing list message matching “coral”"
    )


@pytest.mark.django_db
def test_page_links_leave_out_the_bars_empty_fields(client, project, pm_user, user):
    _bulk_log(project, user, 60)
    client.force_login(pm_user)

    # What the bar submits with nothing chosen.
    response = client.get(_log_url(project, q="", member="", kind="", per_page=25))

    assert response.context["filter_query"] == "per_page=25"


# ---------------------------------------------------------------------------
# Who triggered an email
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_log_shows_who_triggered_each_email(client, project, pm_user, user):
    _add_member(project, user)
    _log(project, user, subject="By a Lead", triggered_by=pm_user)
    _log(project, user, subject="Not recorded")
    client.force_login(pm_user)

    content = client.get(_log_url(project)).content.decode()

    assert "Triggered by" in content
    assert reverse("public_profile", kwargs={"pk": pm_user.pk}) in content
    assert 'title="Not recorded"' in content


@pytest.mark.django_db
def test_the_modal_shows_who_triggered_the_email(client, project, pm_user, user):
    _add_member(project, user)
    email = _log(project, user, triggered_by=pm_user, text_body="Hi")
    client.force_login(pm_user)

    content = client.get(_detail_url(project, email), HTTP_HX_REQUEST="true").content.decode()

    assert "Triggered by" in content
    assert pm_user.email in content


@pytest.mark.django_db
def test_an_automatic_email_says_so(client, project, pm_user, user):
    email = _log(project, user, kind=EmailKind.DIGEST, text_body="Your week")
    client.force_login(pm_user)

    content = client.get(_detail_url(project, email), HTTP_HX_REQUEST="true").content.decode()

    assert "Automatic" in content


@pytest.mark.django_db
def test_search_finds_an_email_by_who_triggered_it(
    client, project, pm_user, user, other_user
):
    _add_member(project, user)
    _log(project, user, subject="From the Lead", triggered_by=pm_user)
    _log(project, user, subject="From someone else", triggered_by=other_user)
    client.force_login(pm_user)

    response = client.get(_log_url(project, q=pm_user.email))

    assert [e.subject for e in response.context["emails"]] == ["From the Lead"]
