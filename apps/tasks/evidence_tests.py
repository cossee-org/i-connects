"""Tests for the evidence an assignee attaches when marking their part done.

The evidence is a list of items — each a link, a note, or both — on the
assignee's TaskLog (``TaskEvidence``). It is shown to the assignee themselves, the task's current reviewers and the
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
from apps.tasks.models import MAX_EVIDENCE_ITEMS, KanbanBoard, Task, TaskEvidence, TaskLog
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
URL2 = "https://github.com/org/repo/pull/7"
NOTE2 = "The pull request with the cleaning script."
TWO = [(URL, NOTE), (URL2, NOTE2)]


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


def _items(task, member):
    log = _log(task, member)
    return list(log.evidence_items.values_list("url", "note"))


def _rows_data(items):
    """The dialog's post for ``items``: one url and one note per row."""
    return {
        "evidence_list": "1",
        "evidence_url": [url for url, _ in items],
        "evidence_note": [note for _, note in items],
    }


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
def test_marking_done_saves_the_list_with_the_mark(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_rows_data([(URL, f"  {NOTE} "), (URL2, NOTE2)]))
    assert response.status_code == 200
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED
    assert _items(s["task"], s["bob"]) == TWO
    assert list(log.evidence_items.values_list("position", flat=True)) == [0, 1]
    assert log.evidence_updated_at is not None
    entry = AuditLog.objects.get(action=AuditAction.TASK_EVIDENCE_SET)
    assert entry.metadata["evidence_urls"] == [URL, URL2]
    assert entry.metadata["evidence_count"] == 2
    assert entry.metadata["assignee_member_id"] == s["bob"].pk


@pytest.mark.django_db
def test_items_can_be_a_link_or_a_note_and_empty_rows_are_dropped(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"], **_rows_data([("", ""), (URL, ""), ("  ", " "), ("", NOTE)]))
    assert _items(s["task"], s["bob"]) == [(URL, ""), ("", NOTE)]


@pytest.mark.django_db
def test_evidence_is_optional(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    assert _complete(client, s["task"], **_rows_data([("", "")])).status_code == 200
    assert _log(s["task"], s["bob"]).status == TaskStatus.COMPLETED
    assert _items(s["task"], s["bob"]) == []
    assert not AuditLog.objects.filter(action=AuditAction.TASK_EVIDENCE_SET).exists()
    # A post without any evidence fields (an older page, a script) still
    # marks done, and leaves what evidence there is alone.
    reopen_own(task=s["task"], member=s["bob"])
    set_evidence_on = _log(s["task"], s["bob"])
    TaskEvidence.objects.create(task_log=set_evidence_on, url=URL, position=0)
    assert _complete(client, s["task"]).status_code == 200
    assert _items(s["task"], s["bob"]) == [(URL, "")]


@pytest.mark.django_db
def test_marking_done_with_every_row_removed_clears_kept_evidence(client, setup):
    """The dialog marks its post with evidence_list, so a list whose rows
    were all removed is read as an empty list, not as no list at all."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"], evidence_list="1")
    assert _items(s["task"], s["bob"]) == []


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "data:text/html,hi", "ftp://example.org/x", "not a link"],
)
@pytest.mark.django_db
def test_only_http_links_are_taken_and_the_item_is_named(client, setup, bad):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_rows_data([(URL, NOTE), (bad, "")]))
    assert response.status_code == 400
    assert response.content.decode().startswith("Item 2:")
    log = _log(s["task"], s["bob"])
    # Refused whole: the mark is not set, and no item is kept.
    assert log.status != TaskStatus.COMPLETED and _items(s["task"], s["bob"]) == []


@pytest.mark.django_db
def test_an_overlong_note_is_refused(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _complete(client, s["task"], **_rows_data([(URL, "x" * 1001)]))
    assert response.status_code == 400 and b"1000" in response.content


@pytest.mark.django_db
def test_at_most_ten_items(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    ten = [(f"https://example.org/{i}", "") for i in range(MAX_EVIDENCE_ITEMS)]
    assert _complete(client, s["task"], **_rows_data(ten)).status_code == 200
    assert len(_items(s["task"], s["bob"])) == MAX_EVIDENCE_ITEMS
    eleven = ten + [("https://example.org/extra", "")]
    response = _set(client, s["task"], **_rows_data(eleven))
    assert response.status_code == 400 and b"at most 10" in response.content
    assert len(_items(s["task"], s["bob"])) == MAX_EVIDENCE_ITEMS
    with pytest.raises(ValueError):
        set_evidence(task_log=_log(s["task"], s["bob"]), items=eleven)


@pytest.mark.django_db
def test_saving_replaces_the_whole_list(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    assert _set(client, s["task"], **_rows_data(TWO)).status_code == 200
    assert _items(s["task"], s["bob"]) == TWO
    # Reordered, one changed, one dropped, one added.
    _set(client, s["task"], **_rows_data([(URL2, "changed"), ("", "only a note")]))
    assert _items(s["task"], s["bob"]) == [(URL2, "changed"), ("", "only a note")]
    # An empty list clears it.
    _set(client, s["task"], evidence_list="1")
    assert _items(s["task"], s["bob"]) == []
    assert AuditLog.objects.filter(action=AuditAction.TASK_EVIDENCE_SET).count() == 3


@pytest.mark.django_db
def test_saving_the_same_list_writes_nothing(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    before = _log(s["task"], s["bob"]).evidence_updated_at
    client.force_login(s["users"]["bob"])
    _set(client, s["task"], **_rows_data(TWO))
    assert _log(s["task"], s["bob"]).evidence_updated_at == before
    assert AuditLog.objects.filter(action=AuditAction.TASK_EVIDENCE_SET).count() == 1


@pytest.mark.django_db
def test_evidence_needs_a_part_marked_done(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], **_rows_data([(URL, "")]))
    assert response.status_code == 400
    assert _items(s["task"], s["bob"]) == []


@pytest.mark.django_db
def test_only_the_assignee_gives_evidence_for_their_part(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"])
    # A reviewer, a Lead who is not on the task, a plain member: none of them
    # is an assignee, so none of them has a part to give evidence for.
    for name in ("carol", "alice", "eve"):
        client.force_login(s["users"][name])
        assert _set(client, s["task"], **_rows_data([(URL, "")])).status_code == 403
        assert _complete(client, s["task"], **_rows_data([(URL, "")])).status_code == 403
    assert _items(s["task"], s["bob"]) == []


@pytest.mark.django_db
def test_a_lead_marking_someone_done_leaves_their_evidence_alone(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["alice"])
    client.post(
        reverse("tasks:member_done", kwargs={"pk": s["task"].pk, "member_pk": s["bob"].pk}),
        {"done": "1", "pane_mode": "1"},
        headers={"hx-request": "true"},
    )
    log = _log(s["task"], s["bob"])
    assert log.status == TaskStatus.COMPLETED and log.completed_by == s["lead"]
    assert _items(s["task"], s["bob"]) == TWO


# ---------------------------------------------------------------------------
# Freezing under a review or a verification
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_review_freezes_the_evidence_until_the_mark_is_undone(client, setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    mark_reviewed(task_log=log, reviewer=s["carol"])
    other = [("https://example.org/other", "")]
    client.force_login(s["users"]["bob"])
    response = _set(client, s["task"], **_rows_data(other))
    assert response.status_code == 400 and b"Untick Done" in response.content
    # Re-marking an already-done, reviewed part does not slip a change in.
    _complete(client, s["task"], **_rows_data(other))
    assert _items(s["task"], s["bob"]) == TWO

    # Undoing clears the review but keeps the items, so the dialog comes back
    # filled in; marking again can then change them.
    client.post(reverse("tasks:undo", kwargs={"pk": s["task"].pk}))
    assert not _log(s["task"], s["bob"]).is_reviewed
    assert _items(s["task"], s["bob"]) == TWO
    pane = _pane(client, s)
    modal = pane[pane.index('data-evidence-modal="done"') :]
    modal = modal[: modal.index("</dialog>")]
    assert URL in modal and URL2 in modal and NOTE2 in modal
    _complete(client, s["task"], **_rows_data(other))
    assert _items(s["task"], s["bob"]) == other


@pytest.mark.django_db
def test_a_verification_freezes_the_evidence(setup):
    s = setup
    log = mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    verify_part(task_log=log, lead=s["lead"])
    with pytest.raises(EvidenceLocked):
        set_evidence(task_log=_log(s["task"], s["bob"]), items=[])
    assert _items(s["task"], s["bob"]) == TWO


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


@pytest.mark.django_db
def test_reviewers_leads_and_the_assignee_see_every_item_and_nobody_else(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    mark_own_done(task=s["task"], member=s["dave"])
    for name in ("alice", "carol", "bob"):
        client.force_login(s["users"][name])
        row = _row_for(_pane(client, s), s["bob"])
        # View evidence carries the list as JSON for the viewer dialog,
        # rather than linking straight to anything from the table.
        assert "data-view-evidence" in row and "View evidence (2)" in row, name
        for text in (URL, NOTE, URL2, NOTE2):
            assert text in row, (name, text)
        assert 'type="application/json"' in row
        assert f'href="{URL}"' not in row
    # Another assignee on the same task, and a plain project member, get no
    # trace of it — not the links, not the notes, not a marker.
    for name in ("dave", "eve"):
        client.force_login(s["users"][name])
        pane = _pane(client, s)
        for text in (URL, NOTE, URL2, NOTE2):
            assert text not in pane, (name, text)
        assert f'data-evidence="{s["bob"].pk}"' not in _rows(pane), name


@pytest.mark.django_db
def test_a_single_item_shows_no_count(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=[(URL, "")])
    client.force_login(s["users"]["carol"])
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-evidence" in row and "View evidence (" not in row


@pytest.mark.django_db
def test_a_reviewer_taken_off_the_task_stops_seeing_it(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    s["task"].reviewers.remove(s["carol"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_evidence_shows_only_on_a_part_marked_done(client, setup):
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    reopen_own(task=s["task"], member=s["bob"])
    client.force_login(s["users"]["carol"])
    assert URL not in _pane(client, s)


@pytest.mark.django_db
def test_view_evidence_is_the_only_evidence_control_in_the_table(client, setup):
    """The table offers View evidence and nothing else: no link, no note, no
    Add / Edit. Those live in the viewer dialog, whose Edit is offered only
    on the viewer's own part until it is reviewed or verified."""
    s = setup
    client.force_login(s["users"]["bob"])
    _complete(client, s["task"])
    # Their own done part with no evidence yet still gets View evidence, so
    # they can open the dialog and add some from there.
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-evidence" in row and "data-can-edit-evidence" in row
    assert "data-edit-evidence" not in row
    _set(client, s["task"], **_rows_data(TWO))
    pane = _pane(client, s)
    row = _row_for(pane, s["bob"])
    assert row.count("<button") == 1 and "<a " not in row and "<details" not in row
    assert "data-can-edit-evidence" in row
    # The viewer dialog carries the edit action, handing over to the
    # evidence dialog.
    modal = pane[pane.index("data-evidence-view-modal") :]
    assert "data-edit-evidence" in modal[: modal.index("</dialog>")]
    assert 'data-evidence-modal="edit"' in pane

    mark_reviewed(task_log=_log(s["task"], s["bob"]), reviewer=s["carol"])
    row = _row_for(_pane(client, s), s["bob"])
    assert "data-view-evidence" in row and "data-can-edit-evidence" not in row
    # Nobody else may edit it, even a Lead.
    client.force_login(s["users"]["alice"])
    assert "data-can-edit-evidence" not in _rows(_pane(client, s))


@pytest.mark.django_db
def test_notes_and_links_are_escaped_in_the_json(client, setup):
    s = setup
    evil = "</script><script>x()</script>"
    mark_own_done(task=s["task"], member=s["bob"], evidence=[(URL, evil)])
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)
    assert evil not in pane and "<script>x()" not in pane
    assert "\\u003C/script\\u003E\\u003Cscript\\u003Ex()" in pane


@pytest.mark.django_db
def test_the_status_block_loads_evidence_in_one_query(client, setup):
    """However many items there are, loading them costs the same queries."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    mark_own_done(task=s["task"], member=s["dave"], evidence=[(URL, "")])
    client.force_login(s["users"]["alice"])
    url = reverse("tasks:status_block", kwargs={"pk": s["task"].pk}) + "?pane=1"
    with CaptureQueriesContext(connection) as one:
        client.get(url)
    TaskEvidence.objects.bulk_create(
        TaskEvidence(task_log=_log(s["task"], s["dave"]), url=f"https://example.org/{i}", position=i)
        for i in range(1, 6)
    )
    with CaptureQueriesContext(connection) as two:
        client.get(url)
    assert len(two) == len(one)


@pytest.mark.django_db
def test_the_viewer_dialog_lists_items_and_warns_before_external_links(client, setup):
    """The dialog lists every item with its own Open link (a new tab without
    the opener or referrer), tags external ones, and carries the site's
    leaving notice for when any is external."""
    s = setup
    mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
    client.force_login(s["users"]["alice"])
    pane = _pane(client, s)
    modal = pane[pane.index("data-evidence-view-modal") :]
    modal = modal[: modal.index("</dialog>")]
    assert 'x-for="(item, i) in items"' in modal and "data-evidence-view-item" in modal
    assert "data-evidence-external-tag" in modal and 'x-show="anyExternal"' in modal
    assert "We are not responsible for the content of external websites." in modal
    assert "location.host" in modal
    assert 'target="_blank" rel="noopener noreferrer nofollow"' in modal
    assert "data-evidence-open" in modal


@pytest.mark.django_db
def test_the_evidence_dialog_offers_rows_up_to_the_limit(client, setup):
    s = setup
    client.force_login(s["users"]["bob"])
    pane = _pane(client, s)
    modal = pane[pane.index("data-evidence-modal=") :]
    modal = modal[: modal.index("</dialog>")]
    assert 'name="evidence_list"' in modal
    assert 'x-for="(r, i) in rows"' in modal and "data-evidence-add" in modal
    assert "data-evidence-remove" in modal and f"limit: {MAX_EVIDENCE_ITEMS}" in modal


# ---------------------------------------------------------------------------
# Not leaked elsewhere
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_no_notification_or_email_carries_it(client, setup, django_capture_on_commit_callbacks):
    s = setup
    with django_capture_on_commit_callbacks(execute=True):
        log = mark_own_done(task=s["task"], member=s["bob"], evidence=TWO)
        mark_reviewed(task_log=log, reviewer=s["carol"])
        verify_part(task_log=log, lead=s["lead"])
    assert Notification.objects.exists()  # the flow does notify people
    for notification in Notification.objects.all():
        text = f"{notification.message} {notification.route_data}"
        for secret in (URL, NOTE, URL2, NOTE2):
            assert secret not in text
    for message in mail.outbox:
        for secret in (URL, NOTE, URL2, NOTE2):
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
        set_evidence(task_log=log, items=TWO)
    assert sent, "the mark itself is still announced"
    for secret in (URL, NOTE, URL2, NOTE2):
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
