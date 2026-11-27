"""Tests for the attachments an assignee attaches when marking their part done.

The attachments are one note (``TaskLog.attachment_note``) and a list of links
(``TaskAttachment``) on the assignee's TaskLog. It is shown to the assignee themselves, the task's current reviewers and the
project's Leads, and to nobody else — not in the Completion table, not in
notifications, emails or the project's Zulip topic.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
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
from apps.tasks.models import MAX_ATTACHMENTS, KanbanBoard, Task, TaskAttachment, TaskLog
from apps.tasks.services import (
    Attachments,
    AttachmentsLocked,
    mark_own_done,
    mark_reviewed,
    reopen_own,
    set_attachments,
    verify_part,
)

URL = "https://drive.example.org/folder/evidence-42"
NOTE = "Analysis notebook and the figures for section 3."
URL2 = "https://github.com/org/repo/pull/7"
NOTE2 = "The pull request with the cleaning script."
LINKS = [URL, URL2]


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


def _evidence(task, member):
    """The member's saved attachments, as ``(links, note)``."""
    log = _log(task, member)
    return list(log.attachments.values_list("url", flat=True)), log.attachment_note


def _form(links=(), note=""):
    """The dialog's post: the note, and one link per row of its table."""
    return {"attachment_list": "1", "attachment_url": list(links), "attachment_note": note}


EV = Attachments(LINKS, NOTE)


def _set(client, task, **data):
    return client.post(
        reverse("tasks:attachments", kwargs={"pk": task.pk}),
        {"pane_mode": "1", **data},
        headers={"hx-request": "true"},
    )


# ---------------------------------------------------------------------------
# Saving it
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_marking_done_saves_the_note_and_links_with_the_mark(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_form([URL, f" {URL2} "], f"  {NOTE} "))
    assert response.status_code == 200
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)
    assert list(log.attachments.values_list("position", flat=True)) == [0, 1]
    assert log.attachments_updated_at is not None
    entry = AuditLog.objects.get(action=AuditAction.TASK_ATTACHMENTS_SET)
    assert entry.metadata["attachment_urls"] == LINKS
    assert entry.metadata["attachment_count"] == 2 and entry.metadata["has_note"] is True
    assert entry.metadata["assignee_member_id"] == s["bob"].pk


@pytest.mark.django_db
def test_a_note_alone_or_links_alone_will_do_and_blank_rows_are_dropped(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"], **_form(["", "  "], NOTE))
    assert _evidence(s["task"], s["bob"]) == ([], NOTE)
    _set(client, s["task"], **_form(["", URL, " ", URL2], ""))
    assert _evidence(s["task"], s["bob"]) == (LINKS, "")


@pytest.mark.django_db
def test_evidence_is_optional(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    assert _complete(client, s["task"], **_form([""], "")).status_code == 200
    assert _log(s["task"], s["bob"]).status == TaskStatus.COMPLETED
    assert _evidence(s["task"], s["bob"]) == ([], "")
    assert not AuditLog.objects.filter(action=AuditAction.TASK_ATTACHMENTS_SET).exists()
    # A post without any attachments fields (an older page, a script) still
    # marks done, and leaves what attachments there is alone.
    reopen_own(task=s["task"], member=s["bob"])
    log = _log(s["task"], s["bob"])
    log.attachment_note = NOTE
    log.save()
    TaskAttachment.objects.create(task_log=log, url=URL, position=0)
    assert _complete(client, s["task"]).status_code == 200
    assert _evidence(s["task"], s["bob"]) == ([URL], NOTE)


@pytest.mark.django_db
def test_marking_done_with_everything_cleared_clears_kept_evidence(client, setup):
    """The dialog marks its post with attachment_list, so attachments whose links
    were all removed is read as empty, not as no attachments sent at all."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"], attachment_list="1", attachment_note="")
    assert _evidence(s["task"], s["bob"]) == ([], "")


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "data:text/html,hi", "ftp://example.org/x", "not a link"],
)
@pytest.mark.django_db
def test_only_http_links_are_taken_and_the_row_is_named(client, setup, bad):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_form([URL, bad], NOTE))
    assert response.status_code == 400
    assert response.content.decode().startswith("Link 2:")
    log = _log(s["task"], s["bob"])
    # Refused whole: the mark is not set, and nothing is kept.
    assert log.status != TaskStatus.COMPLETED
    assert _evidence(s["task"], s["bob"]) == ([], "")


@pytest.mark.django_db
def test_an_overlong_note_is_refused(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_form([URL], "x" * 1001))
    assert response.status_code == 400
    assert response.content.decode().startswith("Note:") and b"1000" in response.content


@pytest.mark.django_db
def test_at_most_ten_links(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    ten = [f"https://example.org/{i}" for i in range(MAX_ATTACHMENTS)]
    assert _complete(client, s["task"], **_form(ten)).status_code == 200
    assert len(_evidence(s["task"], s["bob"])[0]) == MAX_ATTACHMENTS
    eleven = ten + ["https://example.org/extra"]
    response = _set(client, s["task"], **_form(eleven))
    assert response.status_code == 400 and b"at most 10" in response.content
    assert len(_evidence(s["task"], s["bob"])[0]) == MAX_ATTACHMENTS
    with pytest.raises(ValueError):
        set_attachments(task_log=_log(s["task"], s["bob"]), attachments=Attachments(eleven))


@pytest.mark.django_db
def test_saving_replaces_the_whole_evidence(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    assert _set(client, s["task"], **_form(LINKS, NOTE)).status_code == 200
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)
    # Reordered, one dropped, one added, the note changed.
    _set(client, s["task"], **_form([URL2, "https://example.org/new"], NOTE2))
    assert _evidence(s["task"], s["bob"]) == ([URL2, "https://example.org/new"], NOTE2)
    # Only the note changed: the links stay as they were.
    _set(client, s["task"], **_form([URL2, "https://example.org/new"], ""))
    assert _evidence(s["task"], s["bob"]) == ([URL2, "https://example.org/new"], "")
    # Emptied, it clears.
    _set(client, s["task"], attachment_list="1", attachment_note="")
    assert _evidence(s["task"], s["bob"]) == ([], "")
    assert AuditLog.objects.filter(action=AuditAction.TASK_ATTACHMENTS_SET).count() == 4


@pytest.mark.django_db
def test_saving_the_same_evidence_writes_nothing(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    before = _log(s["task"], s["bob"]).attachments_updated_at
    client.force_login(s["users"]["bob"])
    _set(client, s["task"], **_form(LINKS, NOTE))
    assert _log(s["task"], s["bob"]).attachments_updated_at == before
    assert AuditLog.objects.filter(action=AuditAction.TASK_ATTACHMENTS_SET).count() == 1


@pytest.mark.django_db
def test_evidence_needs_a_part_marked_done(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], **_form([URL]))
    assert response.status_code == 400
    assert _evidence(s["task"], s["bob"]) == ([], "")


@pytest.mark.django_db
def test_only_the_assignee_gives_evidence_for_their_part(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"])
    # A reviewer, a Lead who is not on the task, a plain member: none of them
    # is an assignee, so none of them has a part to give attachments for.
    for name in ("carol", "alice", "eve"):
        client.force_login(s["users"][name])
        assert _set(client, s["task"], **_form([URL], NOTE)).status_code == 403
        assert _complete(client, s["task"], **_form([URL], NOTE)).status_code == 403
    assert _evidence(s["task"], s["bob"]) == ([], "")


@pytest.mark.django_db
def test_a_lead_marking_someone_done_leaves_their_evidence_alone(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["alice"])
    client.post(
        reverse("tasks:member_done", kwargs={"pk": s["task"].pk, "member_pk": s["bob"].pk}),
        {"done": "1", "pane_mode": "1"},
        headers={"hx-request": "true"},
    )
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED and log.completed_by == s["lead"]
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)


# ---------------------------------------------------------------------------
# Freezing under a review or a verification
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_review_freezes_the_evidence_until_the_mark_is_undone(client, setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    mark_reviewed(task_log=log, reviewer=s["carol"])
    other = _form(["https://example.org/other"], "changed")
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], **other)
    assert response.status_code == 400 and b"Untick Done" in response.content
    # Re-marking an already-done, reviewed part does not slip a change in.
    _complete(client, s["task"], **other)
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)

    # Undoing clears the review but keeps the attachments, so the dialog comes
    # back filled in; marking again can then change it.
    client.post(reverse("tasks:undo", kwargs={"pk": s["task"].pk}))
    assert not _log(s["task"], s["bob"]).is_reviewed
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)
    pane = _pane(client, s)
    modal = pane[pane.index('data-attachments-modal="done"') :]
    modal = modal[: modal.index("</dialog>")]
    assert URL in modal and URL2 in modal and NOTE in modal
    _complete(client, s["task"], **other)
    assert _evidence(s["task"], s["bob"]) == (["https://example.org/other"], "changed")


@pytest.mark.django_db
def test_a_verification_freezes_the_evidence(setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    verify_part(task_log=log, lead=s["lead"])
    with pytest.raises(AttachmentsLocked):
        set_attachments(task_log=_log(s["task"], s["bob"]), attachments=Attachments([]))
    assert _evidence(s["task"], s["bob"]) == (LINKS, NOTE)


# ---------------------------------------------------------------------------
# Who sees it
# ---------------------------------------------------------------------------
def _rows(pane):
    start = pane.index("data-assignee-rows")
    return pane[start : pane.index("</table>", start)]


def _row_for(pane, member):
    body = _rows(pane)
    start = body.index(f"data-assignee-name>{member.display_name}<")
    return body[body.rindex("<tr", 0, start) : body.index("</tr>", start)]


def _modal(pane):
    start = pane.index("data-attachments-modal")
    return pane[start : pane.index("</dialog>", start)]


@pytest.mark.django_db
def test_reviewers_leads_and_the_assignee_see_it_and_nobody_else(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    mark_own_done(task=s["task"], member=s["dave"])
    for name in ("alice", "carol", "bob"):
        client.force_login(s["users"][name])
        row = _row_for(_pane(client, s), s["bob"])
        # View attachments carries the attachments as JSON for the dialog, rather
        # than linking straight to anything from the table.
        assert "data-view-attachments" in row and "View attachments (2)" in row, name
        for text in (URL, URL2, NOTE):
            assert text in row, (name, text)
        assert 'type="application/json"' in row
        assert f'href="{URL}"' not in row
    # Another assignee on the same task, and a plain project member, get no
    # trace of it — not the links, not the note, not a marker.
    for name in ("dave", "eve"):
        client.force_login(s["users"][name])
        pane = _pane(client, s)
        for text in (URL, URL2, NOTE):
            assert text not in pane, (name, text)
        assert f'data-attachments="{s["bob"].pk}"' not in _rows(pane), name


@pytest.mark.django_db
def test_a_single_link_shows_no_count(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=Attachments([URL], NOTE))
    client.force_login(s["users"]["carol"])
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-attachments" in row and "View attachments (" not in row


@pytest.mark.django_db
def test_a_note_without_links_can_still_be_viewed(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=Attachments([], NOTE))
    client.force_login(s["users"]["carol"])
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-attachments" in row and NOTE in row


@pytest.mark.django_db
def test_a_reviewer_taken_off_the_task_stops_seeing_it(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    s["task"].reviewers.remove(s["carol"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_evidence_shows_only_on_a_part_marked_done(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_view_evidence_is_the_only_evidence_control_in_the_table(client, setup):
    """The table offers View attachments and nothing else: no link, no note, no
    Add / Edit. Those live in the attachments dialog, whose Edit is offered only
    on the viewer's own part until it is reviewed or verified."""
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    # Their own done part with no attachments yet still gets View attachments, so
    # they can open the dialog and add some from there.
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-attachments" in row and "data-can-edit-attachments" in row
    assert "data-edit-attachments" not in row
    _set(client, s["task"], **_form(LINKS, NOTE))
    pane = _pane(client, s)
    row = _row_for(pane, s["bob"])
    # The paperclip, and the chevron that opens the person's details row.
    assert row.count("<button") == 2 and "data-details-toggle" in row
    assert "<a " not in row and "<details" not in row
    assert "data-can-edit-attachments" in row
    # The dialog is where it is both read and edited.
    modal = _modal(pane)
    assert 'data-attachments-modal="edit"' in modal
    assert "data-edit-attachments" in modal and "data-attachments-form" in modal

    mark_reviewed(task_log=_log(s["task"], s["bob"]), reviewer=s["carol"])
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-attachments" in row and "data-can-edit-attachments" not in row
    # Nobody else may edit it, even a Lead: their dialog has no form at all.
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)
    assert "data-can-edit-attachments" not in _rows(pane)
    assert "data-attachments-form" not in _modal(pane)


@pytest.mark.django_db
def test_the_note_and_links_are_escaped_in_the_json(client, setup):
    s = setup
    evil = "</script><script>x()</script>"
    mark_own_done(task=s["task"], member=s["bob"], attachments=Attachments([URL], evil))
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)
    assert evil not in pane and "<script>x()" not in pane
    assert "\\u003C/script\\u003E\\u003Cscript\\u003Ex()" in pane


@pytest.mark.django_db
def test_the_status_block_loads_evidence_in_one_query(client, setup):
    """However many links there are, loading them costs the same queries."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    mark_own_done(task=s["task"], member=s["dave"], attachments=Attachments([URL]))
    client.force_login(s["users"]["alice"])
    url = reverse("tasks:status_block", kwargs={"pk": s["task"].pk}) + "?pane=1"
    with CaptureQueriesContext(connection) as one:
        client.get(url)
    TaskAttachment.objects.bulk_create(
        TaskAttachment(task_log=_log(s["task"], s["dave"]), url=f"https://example.org/{i}", position=i)
        for i in range(1, 6)
    )
    with CaptureQueriesContext(connection) as two:
        client.get(url)
    assert len(two) == len(one)


@pytest.mark.django_db
def test_the_dialog_shows_a_table_of_links_then_the_note(client, setup):
    """Read mode: the links as a table, each row with its own Open (a new tab
    without the opener or referrer) — Open external on an off-site link —
    then the note; Last updated in the header."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
    client.force_login(s["users"]["alice"])
    view = _modal(_pane(client, s))
    view = view[view.index("data-attachments-view>") :]
    assert 'x-for="(url, i) in links"' in view and "data-attachments-view-row" in view
    # No External pill beside the links: the button says it instead.
    assert "data-attachments-external-tag" not in view and ">External</span>" not in view
    # No leaving notice in the dialog, behind an (i) or otherwise: the
    # Open external button is what says a link leaves the site.
    assert "data-attachments-external-info" not in view and "data-attachments-external>" not in view
    assert "We are not responsible for the content of external websites." not in view
    # The note comes after the links.
    assert view.index("data-attachments-view-table") < view.index("data-attachments-view-note")
    # An external link's button says so, with an icon; others say Open.
    assert "isExternal(url) ? 'Open external' : 'Open'" in view
    assert '<svg x-show="isExternal(url)"' in view
    # Last updated is in the header, under the title, before the body.
    modal = _modal(_pane(client, s))
    assert modal.index("attachments-modal-title") < modal.index("data-attachments-updated") < modal.index("data-attachments-view>")
    assert 'target="_blank" rel="noopener noreferrer nofollow"' in view
    assert "data-attachments-open" in view


@pytest.mark.django_db
def test_the_edit_form_has_the_note_above_a_table_of_link_rows(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    form = _modal(_pane(client, s))
    form = form[form.index("data-attachments-form") :]
    assert 'name="attachment_list"' in form
    assert form.index('name="attachment_note"') < form.index("data-attachments-edit-table")
    assert 'x-for="(r, i) in draft.rows"' in form and 'name="attachment_url"' in form
    assert "data-attachments-add" in form and "data-attachments-remove" in form
    assert f"limit: {MAX_ATTACHMENTS}" in _modal(_pane(client, s))


# ---------------------------------------------------------------------------
# Not leaked elsewhere
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_notification_or_email_carries_it(client, setup, django_capture_on_commit_callbacks):
    s = setup
    with django_capture_on_commit_callbacks(execute=True):
        log = mark_own_done(task=s["task"], member=s["bob"], attachments=EV)
        mark_reviewed(task_log=log, reviewer=s["carol"])
        verify_part(task_log=log, lead=s["lead"])
    assert Notification.objects.exists()  # the flow does notify people
    for notification in Notification.objects.all():
        text = f"{notification.message} {notification.route_data}"
        for secret in (URL, URL2, NOTE):
            assert secret not in text
    for message in mail.outbox:
        for secret in (URL, URL2, NOTE):
            assert secret not in message.body


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
        set_attachments(task_log=log, attachments=EV)
    assert sent, "the mark itself is still announced"
    for secret in (URL, URL2, NOTE):
        assert all(secret not in m for m in sent)


# ---------------------------------------------------------------------------
# The migration from one link and note to a list
# ---------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_the_migration_moves_each_entrys_evidence_into_one_item():
    from django.db.migrations.executor import MigrationExecutor

    before = [("tasks", "0022_taskevidence")]
    after = [("tasks", "0024_remove_tasklog_evidence_fields")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        OldLog = apps.get_model("tasks", "TaskLog")
        # Only TaskLog differs between these states, so the rest is set up
        # with today's models.
        user = _user("m@test.com", "M")
        project = create_project_with_owner(
            user=user,
            form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
        )
        member = project.project_members.first()
        task = Task.objects.create(
            project=project, created_by=member, container=project.containers.first(),
            container_task_order=0, name="T",
        )
        common = {"task_id": task.pk, "assigned_to_id": member.pk}
        with_both = OldLog.objects.create(**common, evidence_url=URL, evidence_note=NOTE)
        note_only = OldLog.objects.create(**common, evidence_note=NOTE2)
        none = OldLog.objects.create(**common)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        Item = apps.get_model("tasks", "TaskEvidence")
        got = {
            log_id: (url, note, position)
            for log_id, url, note, position in Item.objects.values_list(
                "task_log_id", "url", "note", "position"
            )
        }
        assert got == {with_both.pk: (URL, NOTE, 0), note_only.pk: ("", NOTE2, 0)}
        assert none.pk not in got

        # Back again: the first item returns to the old fields.
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        OldLog = apps.get_model("tasks", "TaskLog")
        restored = OldLog.objects.get(pk=with_both.pk)
        assert (restored.evidence_url, restored.evidence_note) == (URL, NOTE)
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.django_db(transaction=True)
def test_the_migration_merges_item_notes_into_one_note():
    """0026: a single item note is kept as it is; several are joined, each
    labelled with its link's number; note-only items go and the remaining
    links are renumbered. Going back puts the note on the first link."""
    from django.db.migrations.executor import MigrationExecutor

    before = [("tasks", "0025_tasklog_evidence_note")]
    after = [("tasks", "0027_remove_taskevidence_note")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        OldLog = apps.get_model("tasks", "TaskLog")
        OldItem = apps.get_model("tasks", "TaskEvidence")
        user = _user("m@test.com", "M")
        project = create_project_with_owner(
            user=user,
            form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
        )
        member = project.project_members.first()
        task = Task.objects.create(
            project=project, created_by=member, container=project.containers.first(),
            container_task_order=0, name="T",
        )
        common = {"task_id": task.pk, "assigned_to_id": member.pk}
        one = OldLog.objects.create(**common)
        OldItem.objects.create(task_log_id=one.pk, url=URL, note=NOTE, position=0)
        OldItem.objects.create(task_log_id=one.pk, url=URL2, note="", position=1)
        many = OldLog.objects.create(**common)
        OldItem.objects.create(task_log_id=many.pk, url="", note="Just a note", position=0)
        OldItem.objects.create(task_log_id=many.pk, url=URL, note=NOTE, position=1)
        OldItem.objects.create(task_log_id=many.pk, url=URL2, note=NOTE2, position=2)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        Log = apps.get_model("tasks", "TaskLog")
        Item = apps.get_model("tasks", "TaskEvidence")

        def links(log_id):
            return list(
                Item.objects.filter(task_log_id=log_id)
                .order_by("position")
                .values_list("url", "position")
            )

        assert Log.objects.get(pk=one.pk).evidence_note == NOTE
        assert links(one.pk) == [(URL, 0), (URL2, 1)]
        assert Log.objects.get(pk=many.pk).evidence_note == (
            f"Just a note\n\nLink 1: {NOTE}\n\nLink 2: {NOTE2}"
        )
        assert links(many.pk) == [(URL, 0), (URL2, 1)]

        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        first = apps.get_model("tasks", "TaskEvidence").objects.get(task_log_id=one.pk, position=0)
        assert (first.url, first.note) == (URL, NOTE)
    finally:
        MigrationExecutor(connection).migrate(leaves)


@pytest.mark.django_db(transaction=True)
def test_the_rename_carries_old_audit_rows_over_to_attachments():
    """audit 0017: rows written as task.evidence_set become
    task.attachments_set, with their metadata keys renamed (a single link
    from the first version becoming a list), and back again."""
    from django.db.migrations.executor import MigrationExecutor

    before = [("audit", "0016_alter_auditlog_action_evidence")]
    after = [("audit", "0017_attachments_action")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(before)
        AuditLog = executor.loader.project_state(before).apps.get_model("audit", "AuditLog")
        single = AuditLog.objects.create(
            action="task.evidence_set", metadata={"task_name": "T", "evidence_url": URL}
        )
        listed = AuditLog.objects.create(
            action="task.evidence_set",
            metadata={"evidence_urls": [URL, URL2], "evidence_count": 2},
        )
        other = AuditLog.objects.create(action="task.part_done", metadata={"evidence_url": URL})

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        AuditLog = executor.loader.project_state(after).apps.get_model("audit", "AuditLog")
        row = AuditLog.objects.get(pk=single.pk)
        assert row.action == "task.attachments_set"
        assert row.metadata == {"task_name": "T", "attachment_urls": [URL]}
        row = AuditLog.objects.get(pk=listed.pk)
        assert row.metadata == {"attachment_urls": [URL, URL2], "attachment_count": 2}
        # Other actions are left as they were.
        assert AuditLog.objects.get(pk=other.pk).metadata == {"evidence_url": URL}

        executor = MigrationExecutor(connection)
        executor.migrate(before)
        AuditLog = executor.loader.project_state(before).apps.get_model("audit", "AuditLog")
        assert AuditLog.objects.get(pk=listed.pk).action == "task.evidence_set"
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_the_attachments_dialog_template_has_balanced_tags():
    """A stray closing tag in the dialog pulls its body out of the padded
    box (it happened once, when the (i) beside Links was taken out)."""
    import re
    from pathlib import Path

    from django.conf import settings

    path = Path(settings.BASE_DIR) / "templates/tasks/partials/task_attachments_modal.html"
    text = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", path.read_text(), flags=re.S)
    text = re.sub(r"{#.*?#}", "", text, flags=re.S)
    for tag in ("div", "section", "form", "table", "template", "dialog"):
        opened = len(re.findall(rf"<{tag}[\s>]", text))
        closed = len(re.findall(rf"</{tag}>", text))
        assert opened == closed, (tag, opened, closed)


@pytest.mark.django_db
def test_view_attachments_sits_under_the_name(client, setup):
    """View attachments joins the custom values on the line under the
    assignee's name, not the Done cell, so the stages' dates stay in line."""
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    _set(client, s["task"], **_form(LINKS, NOTE))

    row = _row_for(_pane(client, s), s["bob"])
    name_cell = row[: row.index("data-done-cell")]
    done_cell = row[row.index("data-done-cell") : row.index("data-verify-cell")]
    assert "data-view-attachments" in name_cell
    assert 'class="completion-under' in name_cell
    assert "data-view-attachments" not in done_cell


@pytest.mark.django_db
def test_details_row_keeps_no_id_for_htmx_to_settle(client, setup):
    """The details row is hidden with x-show; an id on it would let HTMX put
    its old style back after a tick, which opens every closed panel. The id
    the chevron points at sits on the list inside."""
    s = setup
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)

    assert '<tr class="completion-details"\n' in pane
    tr = pane[pane.index('<tr class="completion-details"') :]
    tr = tr[: tr.index(">")]
    assert "id=" not in tr
    assert f'<dl id="completion-details-{s["task"].pk}-{s["bob"].pk}">' in pane
    assert f'aria-controls="completion-details-{s["task"].pk}-{s["bob"].pk}"' in pane
