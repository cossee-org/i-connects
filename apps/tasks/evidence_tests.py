"""Tests for the evidence an assignee attaches when marking their part done.

The evidence (a link and a note) lives on the assignee's TaskLog. It is
shown to the assignee themselves, the task's current reviewers and the
project's Leads, and to nobody else — not in the Completion table, not in
notifications, emails or the project's Zulip topic.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import AuditAction
from apps.core.enums import MemberPrivileges, ProjectStatus, TaskStatus
from apps.integrations import zulip
from apps.integrations.models import ZulipConnection
from apps.notifications.models import Notification
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task, TaskLog
from apps.tasks.services import (
    EvidenceLocked,
    mark_own_done,
    mark_reviewed,
    reopen_own,
    set_evidence,
    verify_part,
)

URL = "https://drive.example.org/folder/evidence-42"
NOTE = "Analysis notebook and the figures for section 3."


def _user(email, name):
    return get_user_model().objects.create_user(
        email=email, password="password123", name=name, email_confirmed_at=timezone.now()
    )


def _member(project, user, privilege=MemberPrivileges.CONTRIBUTOR):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(project=project, user=user, privilege=privilege)
    return member


@pytest.fixture
def setup(user, other_user):
    """A task with two assignees (Bob, Dave) and a reviewer (Carol) who is
    not a Lead; Eve is a plain project member. Alice is the Lead."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    board = KanbanBoard.objects.create(project=project, name="To Do", container_order=0)
    lead = project.project_members.first()
    bob = _member(project, other_user)
    carol_user, dave_user, eve_user = (
        _user("carol@test.com", "Carol"),
        _user("dave@test.com", "Dave"),
        _user("eve@test.com", "Eve"),
    )
    carol = _member(project, carol_user)
    dave = _member(project, dave_user)
    eve = _member(project, eve_user)
    task = Task.objects.create(
        project=project,
        created_by=lead,
        container=board,
        container_task_order=0,
        name="Extract the data",
    )
    task.assigned_members.add(bob, dave)
    task.reviewers.add(carol)
    for member in (bob, dave):
        TaskLog.objects.create(task=task, assigned_to=member)
    return {
        "project": project,
        "task": task,
        "lead": lead,
        "bob": bob,
        "carol": carol,
        "dave": dave,
        "eve": eve,
        "users": {
            "alice": user,
            "bob": other_user,
            "carol": carol_user,
            "dave": dave_user,
            "eve": eve_user,
        },
    }


def _log(task, member):
    return task.task_logs.filter(assigned_to=member).order_by("-created_at").first()


def _pane(client, s):
    url = reverse(
        "tasks:detail_pane", kwargs={"project_pk": s["project"].pk, "pk": s["task"].pk}
    )
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _complete(client, task, **data):
    return client.post(
        reverse("tasks:complete", kwargs={"pk": task.pk}),
        {"pane_mode": "1", **data},
        headers={"hx-request": "true"},
    )


def _set(client, task, **data):
    return client.post(
        reverse("tasks:evidence", kwargs={"pk": task.pk}),
        {"pane_mode": "1", **data},
        headers={"hx-request": "true"},
    )


# ---------------------------------------------------------------------------
# Saving it
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_marking_done_saves_the_evidence_with_the_mark(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], evidence_url=URL, evidence_note=f"  {NOTE} ")
    assert response.status_code == 200
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED
    assert log.evidence_url == URL and log.evidence_note == NOTE
    assert log.evidence_updated_at is not None
    entry = AuditLog.objects.get(action=AuditAction.TASK_EVIDENCE_SET)
    assert entry.metadata["evidence_url"] == URL
    assert entry.metadata["assignee_member_id"] == s["bob"].pk


@pytest.mark.django_db
def test_evidence_is_optional(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    assert _complete(client, s["task"], evidence_url="", evidence_note="").status_code == 200
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED and log.evidence_url == ""
    assert not AuditLog.objects.filter(action=AuditAction.TASK_EVIDENCE_SET).exists()
    # A post without the fields at all (an older page) still marks done.
    reopen_own(task=s["task"], member=s["bob"])
    assert _complete(client, s["task"]).status_code == 200
    assert _log(s["task"], s["bob"]).status == TaskStatus.COMPLETED


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "data:text/html,hi", "ftp://example.org/x", "not a link"],
)
@pytest.mark.django_db
def test_only_http_links_are_taken(client, setup, bad):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], evidence_url=bad)
    assert response.status_code == 400
    log = _log(s["task"], s["bob"])
    # Refused whole: the mark is not set either, so nothing half-happens.
    assert log.status != TaskStatus.COMPLETED and log.evidence_url == ""


@pytest.mark.django_db
def test_an_overlong_note_is_refused(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], evidence_url=URL, evidence_note="x" * 1001)
    assert response.status_code == 400 and b"1000" in response.content


@pytest.mark.django_db
def test_the_assignee_can_add_change_and_clear_it_after_marking(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    assert _set(client, s["task"], evidence_url=URL, evidence_note=NOTE).status_code == 200
    assert _log(s["task"], s["bob"]).evidence_url == URL
    other = "https://github.com/org/repo/pull/7"
    _set(client, s["task"], evidence_url=other, evidence_note="")
    log = _log(s["task"], s["bob"])
    assert log.evidence_url == other and log.evidence_note == ""
    _set(client, s["task"], evidence_url="", evidence_note="")
    assert _log(s["task"], s["bob"]).evidence_url == ""
    assert AuditLog.objects.filter(action=AuditAction.TASK_EVIDENCE_SET).count() == 3


@pytest.mark.django_db
def test_evidence_needs_a_part_marked_done(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], evidence_url=URL)
    assert response.status_code == 400
    assert _log(s["task"], s["bob"]).evidence_url == ""


@pytest.mark.django_db
def test_only_the_assignee_gives_evidence_for_their_part(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"])
    # A reviewer, a Lead who is not on the task, a plain member: none of them
    # is an assignee, so none of them has a part to give evidence for.
    for name in ("carol", "alice", "eve"):
        client.force_login(s["users"][name])
        assert _set(client, s["task"], evidence_url=URL).status_code == 403
        assert _complete(client, s["task"], evidence_url=URL).status_code == 403
    assert _log(s["task"], s["bob"]).evidence_url == ""


@pytest.mark.django_db
def test_a_lead_marking_someone_done_leaves_their_evidence_alone(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["alice"])
    client.post(
        reverse("tasks:member_done", kwargs={"pk": s["task"].pk, "member_pk": s["bob"].pk}),
        {"done": "1", "pane_mode": "1"},
        headers={"hx-request": "true"},
    )
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED and log.completed_by == s["lead"]
    assert log.evidence_url == URL and log.evidence_note == NOTE


# ---------------------------------------------------------------------------
# Freezing under a review or a verification
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_review_freezes_the_evidence_until_the_mark_is_undone(client, setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    mark_reviewed(task_log=log, reviewer=s["carol"])
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], evidence_url="https://example.org/other")
    assert response.status_code == 400 and b"Untick Done" in response.content
    # Re-marking an already-done, reviewed part does not slip a change in.
    _complete(client, s["task"], evidence_url="https://example.org/other")
    assert _log(s["task"], s["bob"]).evidence_url == URL

    # Undoing clears the review but keeps the evidence, so the dialog comes
    # back filled in; marking again can then change it.
    client.post(reverse("tasks:undo", kwargs={"pk": s["task"].pk}))
    log = _log(s["task"], s["bob"])
    assert not log.is_reviewed and log.evidence_url == URL
    pane = _pane(client, s)
    assert f'value="{URL}"' in pane and 'data-evidence-modal="done"' in pane
    _complete(client, s["task"], evidence_url="https://example.org/other")
    assert _log(s["task"], s["bob"]).evidence_url == "https://example.org/other"


@pytest.mark.django_db
def test_a_verification_freezes_the_evidence(setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    verify_part(task_log=log, lead=s["lead"])
    with pytest.raises(EvidenceLocked):
        set_evidence(task_log=_log(s["task"], s["bob"]), url="", note="")


# ---------------------------------------------------------------------------
# Who sees it
# ---------------------------------------------------------------------------
def _rows(pane):
    start = pane.index("data-assignee-rows")
    return pane[start : pane.index("</table>", start)]


@pytest.mark.django_db
def test_reviewers_leads_and_the_assignee_see_it_and_nobody_else(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    mark_own_done(task=s["task"], member=s["dave"])
    for name in ("alice", "carol", "bob"):
        client.force_login(s["users"][name])
        rows = _rows(_pane(client, s))
        assert URL in rows and NOTE in rows and "data-evidence-link" in rows, name
        assert 'rel="noopener noreferrer nofollow"' in rows
    # Another assignee on the same task, and a plain project member, get no
    # trace of it — not the link, not the note, not a marker.
    for name in ("dave", "eve"):
        client.force_login(s["users"][name])
        pane = _pane(client, s)
        assert URL not in pane and NOTE not in pane, name
        assert f'data-evidence="{s['bob'].pk}"' not in _rows(pane), name


@pytest.mark.django_db
def test_a_reviewer_taken_off_the_task_stops_seeing_it(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    s["task"].reviewers.remove(s["carol"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_evidence_shows_only_on_a_part_marked_done(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_the_assignee_gets_add_then_edit_until_it_is_reviewed(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    pane = _pane(client, s)
    assert "Add evidence" in pane and 'data-evidence-modal="edit"' in pane
    _set(client, s["task"], evidence_url=URL)
    assert "Edit evidence" in _pane(client, s)
    mark_reviewed(task_log=_log(s["task"], s["bob"]), reviewer=s["carol"])
    pane = _pane(client, s)
    assert "Edit evidence" not in pane and URL in pane
    # Nobody else is offered the edit, even a Lead.
    client.force_login(s["users"]["alice"])
    assert "data-edit-evidence" not in _pane(client, s)


@pytest.mark.django_db
def test_the_note_is_escaped(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, "<script>x()</script>"))
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)
    assert "<script>x()</script>" not in pane
    assert "&lt;script&gt;x()&lt;/script&gt;" in pane


# ---------------------------------------------------------------------------
# Not leaked elsewhere
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_notification_or_email_carries_it(client, setup, django_capture_on_commit_callbacks):
    s = setup
    with django_capture_on_commit_callbacks(execute=True):
        log = mark_own_done(task=s["task"], member=s["bob"], evidence=(URL, NOTE))
        mark_reviewed(task_log=log, reviewer=s["carol"])
        verify_part(task_log=log, lead=s["lead"])
    assert Notification.objects.exists()  # the flow does notify people
    for notification in Notification.objects.all():
        text = f"{notification.message} {notification.route_data}"
        assert URL not in text and NOTE not in text
    for message in mail.outbox:
        assert URL not in message.body and NOTE not in message.body


@pytest.mark.django_db
def test_the_zulip_topic_hears_of_the_mark_but_not_the_evidence(
    setup, monkeypatch, django_capture_on_commit_callbacks
):
    s = setup
    ZulipConnection.objects.create(
        project=s["project"],
        site="https://example.zulipchat.com",
        bot_email="bot@example.zulipchat.com",
        api_key="k",
        channel="c",
        topic="t",
    )
    sent: list[str] = []
    monkeypatch.setattr(
        zulip, "deliver", lambda connection, content, **kw: (sent.append(content), (True, ""))[1]
    )
    with django_capture_on_commit_callbacks(execute=True):
        log = mark_own_done(task=s["task"], member=s["bob"])
        set_evidence(task_log=log, url=URL, note=NOTE)
    assert sent, "the mark itself is still announced"
    assert all(URL not in m and NOTE not in m for m in sent)
