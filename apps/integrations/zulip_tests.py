"""Tests for posting a project's activity to its Zulip topic.

``zulip.deliver`` — the one function that touches the network — is replaced
by a recorder, except in the tests of ``deliver`` itself, which fake
``urlopen``. Messages are queued for after the transaction commits, so the
tests run their changes inside ``django_capture_on_commit_callbacks``.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import AuditAction, log_action
from apps.comments.models import Comment
from apps.core.enums import ContributionType, MemberPrivileges, ProjectStatus, TaskStatus
from apps.integrations import zulip
from apps.integrations.models import ZulipConnection
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import (
    accept_invitation,
    create_invitation,
    create_project_with_owner,
)
from apps.tasks.models import KanbanBoard, Task, TaskLog
from apps.tasks.services import close_task, mark_own_done, mark_reviewed
from apps.wiki.models import WikiVisibility
from apps.wiki.services import create_page, update_page

SITE = "https://i-connects.example.org"


@pytest.fixture
def project(pm_user):
    return create_project_with_owner(
        user=pm_user,
        form_data={"name": "Pigauto", "status": ProjectStatus.ACTIVE, "is_public": True},
    )


@pytest.fixture
def lead(project):
    return project.project_members.first()


@pytest.fixture
def alice(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return member


@pytest.fixture
def connection(project):
    return ZulipConnection.objects.create(
        project=project,
        site="https://cossee.zulipchat.com",
        bot_email="iconnects-bot@cossee.zulipchat.com",
        api_key="secret-key",
        channel="shinichi-projects",
        topic="pigauto",
    )


@pytest.fixture
def board(project):
    return KanbanBoard.objects.create(project=project, name="To Do", container_order=0)


@pytest.fixture
def sent(monkeypatch):
    """The content of every message handed to Zulip."""
    outbox: list[str] = []

    def fake_deliver(connection, content, **kwargs):
        outbox.append(content)
        return True, ""

    monkeypatch.setattr(zulip, "deliver", fake_deliver)
    return outbox


def _task(project, board, lead, **fields):
    return Task.objects.create(
        project=project,
        container=board,
        container_task_order=0,
        created_by=lead,
        contribution_type=ContributionType.OTHER,
        **{"name": "Extract the data", **fields},
    )


def _old(task):
    """Age a task past the window in which assignees count as part of
    creating it."""
    Task.objects.filter(pk=task.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )
    task.refresh_from_db()
    return task


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@override_settings(SITE_URL=SITE)
def test_a_new_task_is_announced_once_with_its_assignees_and_deadline(
    client, project, pm_user, alice, board, connection, sent,
    django_capture_on_commit_callbacks,
):
    client.force_login(pm_user)
    deadline = timezone.now() + timedelta(days=10)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("tasks:create", kwargs={"project_pk": project.pk}),
            {
                "name": "Extract the data",
                "container": board.pk,
                "contribution_type": "OTHER",
                "assigned_members": [alice.pk],
                "deadline": deadline.strftime("%Y-%m-%dT%H:%M"),
            },
        )

    assert response.status_code in (200, 302)
    task = Task.objects.get(project=project, name="Extract the data")
    assert list(task.assigned_members.all()) == [alice]
    # One message — the assignee is named in it, not announced separately.
    assert len(sent) == 1
    assert sent[0].startswith("**PM** created the task [Extract the data](")
    assert f"{SITE}/projects/{project.pk}/tasks/{task.pk}/" in sent[0]
    assert "assigned to Alice" in sent[0]
    assert "due " in sent[0]


@pytest.mark.django_db
def test_adding_someone_to_an_existing_task_names_only_the_newcomer(
    project, lead, alice, board, connection, sent, django_capture_on_commit_callbacks
):
    task = _old(_task(project, board, lead))
    task.assigned_members.add(lead)
    sent.clear()

    with django_capture_on_commit_callbacks(execute=True):
        # What the assignees form does: the whole new set.
        task.assigned_members.set([lead, alice])

    assert sent == ["**Extract the data** was assigned to Alice"]

    # Saving the same set again says nothing.
    with django_capture_on_commit_callbacks(execute=True):
        task.assigned_members.set([lead, alice])
    assert len(sent) == 1


@pytest.mark.django_db
def test_csv_imported_tasks_are_not_announced_one_by_one(
    project, lead, pm_user, board, connection, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        task = _task(project, board, lead)
        task.assigned_members.set([lead])
        log_action(
            AuditAction.TASK_CREATED,
            actor=pm_user,
            entity=task,
            project=project,
            metadata={"name": task.name, "via": "csv_import"},
        )

    assert sent == []


@pytest.mark.django_db
def test_progress_on_a_task_is_announced(
    project, lead, alice, board, connection, sent, django_capture_on_commit_callbacks
):
    task = _old(_task(project, board, lead))
    task.assigned_members.add(alice)
    task.reviewers.add(lead)
    sent.clear()

    with django_capture_on_commit_callbacks(execute=True):
        log = mark_own_done(task=task, member=alice)
    assert sent == ["**Alice** marked their part of **Extract the data** as done"]

    with django_capture_on_commit_callbacks(execute=True):
        mark_reviewed(task_log=log, reviewer=lead)
    assert sent[1] == "**PM** reviewed Alice's part of **Extract the data**"
    # The last review closes the task, which is its own news.
    assert sent[2] == "The task **Extract the data** is complete (closed by PM)"
    assert len(sent) == 3


@pytest.mark.django_db
def test_closing_a_task_is_announced(
    project, lead, board, connection, sent, django_capture_on_commit_callbacks
):
    task = _old(_task(project, board, lead))

    with django_capture_on_commit_callbacks(execute=True):
        close_task(task=task, closed_by=lead)

    assert sent == ["The task **Extract the data** is complete (closed by PM)"]


# ---------------------------------------------------------------------------
# People, comments, wiki
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_new_member_is_welcomed_by_name_not_email(
    project, pm_user, other_user, connection, sent, django_capture_on_commit_callbacks
):
    invitation = create_invitation(
        project=project,
        email=other_user.email,
        privilege=MemberPrivileges.CONTRIBUTOR,
        added_by=pm_user.email,
        inviter=pm_user,
    )

    with django_capture_on_commit_callbacks(execute=True):
        accept_invitation(invitation=invitation, user=other_user)

    assert sent == ["**Bob** joined **Pigauto**"]
    assert "bob@test.com" not in sent[0]


@pytest.mark.django_db
def test_a_comment_is_quoted_with_its_mentions_defused(
    project, lead, alice, board, connection, sent, django_capture_on_commit_callbacks
):
    task = _old(_task(project, board, lead))

    with django_capture_on_commit_callbacks(execute=True):
        Comment.objects.create(
            task=task, author=alice, content="Done, @**all** have a look\n```\nx\n```"
        )

    assert len(sent) == 1
    assert sent[0].startswith("**Alice** commented on **Extract the data**:\n```quote\n")
    assert "@**all**" not in sent[0]
    # The comment's own fence cannot close the quote block early.
    assert sent[0].count("```") == 2

    # Editing a comment is not a new comment.
    comment = Comment.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        comment.content = "Edited"
        comment.save()
    assert len(sent) == 1


@pytest.mark.django_db
@override_settings(SITE_URL=SITE)
def test_wiki_pages_are_announced_unless_they_are_for_leads_only(
    project, pm_user, connection, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        page = create_page(
            project=project, actor=pm_user, title="Protocol", content_markdown="v1"
        )
    assert sent == [
        f"**PM** created the wiki page [Protocol]({SITE}/projects/{project.pk}/wiki/protocol/)"
    ]

    with django_capture_on_commit_callbacks(execute=True):
        update_page(page=page, actor=pm_user, title="Protocol", content_markdown="v2")
    assert sent[1].startswith("**PM** edited the wiki page [Protocol](")

    # Changing only a page's settings is not an edit worth a message.
    with django_capture_on_commit_callbacks(execute=True):
        update_page(
            page=page, actor=pm_user, title="Protocol", content_markdown="v2", locked=True
        )
    assert len(sent) == 2

    with django_capture_on_commit_callbacks(execute=True):
        create_page(
            project=project,
            actor=pm_user,
            title="Budget",
            visibility=WikiVisibility.LEADS,
        )
    assert len(sent) == 2


# ---------------------------------------------------------------------------
# Switches
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_an_unconnected_project_posts_nothing(
    project, lead, board, sent, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        close_task(task=_old(_task(project, board, lead)), closed_by=lead)

    assert sent == []


@pytest.mark.django_db
def test_a_paused_connection_posts_nothing(
    project, lead, board, connection, sent, django_capture_on_commit_callbacks
):
    connection.enabled = False
    connection.save()

    with django_capture_on_commit_callbacks(execute=True):
        close_task(task=_old(_task(project, board, lead)), closed_by=lead)

    assert sent == []


@pytest.mark.django_db
def test_each_kind_of_message_can_be_switched_off(
    project, lead, alice, board, connection, sent, django_capture_on_commit_callbacks
):
    connection.notify_comments = False
    connection.save()
    task = _old(_task(project, board, lead))

    with django_capture_on_commit_callbacks(execute=True):
        Comment.objects.create(task=task, author=alice, content="Hello")
        close_task(task=task, closed_by=lead)

    # The comment stayed in i-CONNECTS; the task news still went out.
    assert sent == ["The task **Extract the data** is complete (closed by PM)"]


@pytest.mark.django_db
def test_a_rolled_back_change_announces_nothing(
    project, lead, board, connection, sent, django_capture_on_commit_callbacks
):
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                close_task(task=_old(_task(project, board, lead)), closed_by=lead)
                raise RuntimeError("something later in the request failed")
        except RuntimeError:
            pass

    assert sent == []


@pytest.mark.django_db
def test_zulip_being_down_does_not_break_the_change(
    project, lead, board, connection, monkeypatch, django_capture_on_commit_callbacks
):
    def boom(*args, **kwargs):
        raise RuntimeError("zulip exploded")

    monkeypatch.setattr(zulip, "deliver", boom)
    task = _old(_task(project, board, lead))

    with django_capture_on_commit_callbacks(execute=True):
        close_task(task=task, closed_by=lead)

    task.refresh_from_db()
    assert task.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_the_daily_reminder_lists_what_is_due_soon_and_newly_overdue(
    project, lead, alice, board, connection, sent
):
    from cron.tasks import zulip_deadline_reminder_cron

    now = timezone.now()
    soon = _task(project, board, lead, name="Due soon", deadline=now + timedelta(days=2))
    soon.assigned_members.add(alice)
    _task(project, board, lead, name="Just overdue", deadline=now - timedelta(hours=5))
    # None of these belong in today's reminder.
    _task(project, board, lead, name="Far away", deadline=now + timedelta(days=30))
    _task(project, board, lead, name="Long overdue", deadline=now - timedelta(days=9))
    _task(project, board, lead, name="No deadline")
    _task(
        project, board, lead, name="Already done",
        deadline=now + timedelta(days=1), status=TaskStatus.COMPLETED,
    )
    sent.clear()

    assert zulip_deadline_reminder_cron() == 1

    assert len(sent) == 1
    message = sent[0]
    assert "**Deadlines coming up**\n- **Due soon**" in message
    assert "Alice" in message
    assert "**Now overdue**\n- **Just overdue**" in message
    for absent in ("Far away", "Long overdue", "No deadline", "Already done"):
        assert absent not in message


@pytest.mark.django_db
def test_the_daily_reminder_is_silent_with_nothing_due_or_when_switched_off(
    project, lead, board, connection, sent
):
    from cron.tasks import zulip_deadline_reminder_cron

    assert zulip_deadline_reminder_cron() == 0

    _task(project, board, lead, deadline=timezone.now() + timedelta(days=1))
    connection.notify_deadlines = False
    connection.save()
    sent.clear()

    assert zulip_deadline_reminder_cron() == 0
    assert sent == []


def test_the_deadline_reminder_is_scheduled_under_its_registered_name():
    """Beat sends the task by the name in the schedule; a name the worker
    does not know is dropped without a word."""
    from apps.core.management.commands.setup_cron import SCHEDULE
    from cron.tasks import zulip_deadline_reminder_cron

    scheduled = {name: task for name, task, _ in SCHEDULE}
    assert scheduled["zulip_deadline_reminder"] == zulip_deadline_reminder_cron.name


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def test_plain_makes_user_text_safe_for_a_line_of_markdown():
    assert zulip.plain("  Fix\n the   thing ") == "Fix the thing"
    assert "@**all**" not in zulip.plain("@**all** read this")
    assert zulip.plain("[click](http://evil.example)") == "(click)(http://evil.example)"
    assert zulip.plain("x" * 300).endswith("…")
    assert len(zulip.plain("x" * 300)) == 120


def test_site_validation():
    assert zulip.is_valid_site("https://cossee.zulipchat.com")
    assert not zulip.is_valid_site("http://cossee.zulipchat.com")
    assert not zulip.is_valid_site("https://zulipchat.com.evil.example")
    assert not zulip.is_valid_site("https://127.0.0.1")
    assert not zulip.is_valid_site("")
    with override_settings(ZULIP_ALLOWED_HOST_SUFFIXES=("chat.example.org",)):
        assert zulip.is_valid_site("https://chat.example.org")
        assert not zulip.is_valid_site("https://cossee.zulipchat.com")


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.django_db
def test_deliver_posts_to_the_topic_as_the_bot(connection, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["data"] = dict(urllib.parse.parse_qsl(req.data.decode()))
        return _Response()

    monkeypatch.setattr(zulip.urllib.request, "urlopen", fake_urlopen)

    assert zulip.deliver(connection, "Hello team") == (True, "")
    assert captured["url"] == "https://cossee.zulipchat.com/api/v1/messages"
    assert captured["auth"].startswith("Basic ")
    assert captured["data"] == {
        "type": "stream",
        "to": "shinichi-projects",
        "topic": "pigauto",
        "content": "Hello team",
    }


@pytest.mark.django_db
def test_deliver_reports_what_zulip_said(connection, monkeypatch):
    def refuse(code, msg):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, code, "error", {},
                io.BytesIO(json.dumps({"result": "error", "msg": msg}).encode()),
            )
        return fake_urlopen

    monkeypatch.setattr(zulip.urllib.request, "urlopen", refuse(401, "Invalid API key"))
    ok, detail = zulip.deliver(connection, "Hello")
    assert ok is False and "API key" in detail

    monkeypatch.setattr(
        zulip.urllib.request, "urlopen", refuse(400, "Channel 'nope' does not exist")
    )
    assert zulip.deliver(connection, "Hello") == (False, "Channel 'nope' does not exist")

    def unreachable(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(zulip.urllib.request, "urlopen", unreachable)
    assert zulip.deliver(connection, "Hello") == (False, "Could not reach Zulip.")


@pytest.mark.django_db
def test_deliver_never_calls_a_host_that_is_not_allowed(connection, monkeypatch):
    def fake_urlopen(req, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("urlopen must not be called")

    monkeypatch.setattr(zulip.urllib.request, "urlopen", fake_urlopen)
    connection.site = "https://internal.example"

    ok, _ = zulip.deliver(connection, "Hello")
    assert ok is False
