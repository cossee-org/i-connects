"""Smoke tests for the tasks app."""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import MemberPrivileges, ProjectStatus, TaskStatus
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
from apps.tasks.calendar import (
    bucket_tasks_by_day,
    build_day_tasks,
    build_month_grid,
    build_week_grid,
    build_year_grid,
    clamp_month,
    parse_view_date,
    shift_date,
    task_date_label,
    task_range,
)
from apps.tasks.forms import TaskForm
from apps.tasks.models import KanbanBoard, Task, TaskLog
from apps.tasks.services import approve_completion, complete_task


def _make_project_with_board(owner, *, name="P"):
    project = create_project_with_owner(
        user=owner,
        form_data={"name": name, "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    # Drop the default seeded columns so these tests control the board exactly.
    project.containers.all().delete()
    col1 = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    col2 = KanbanBoard.objects.create(project=project, name="Doing", container_order=1)
    return project, col1, col2


def _primary_member(project, user):
    return ProjectMember.objects.filter(project=project, users=user).first()


@pytest.mark.django_db
def test_task_move_across_columns(client, user):
    project, col1, col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    task = Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="T1",
    )
    client.force_login(user)
    response = client.post(
        reverse("tasks:move", kwargs={"pk": task.pk}),
        {"container_id": col2.pk, "order": 0},
    )
    assert response.status_code == 204
    task.refresh_from_db()
    assert task.container_id == col2.pk


@pytest.mark.django_db
def test_task_move_requires_member_access(client, user, other_user):
    project, col1, col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    task = Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="T2",
    )
    # other_user is NOT a member of this project.
    client.force_login(other_user)
    response = client.post(
        reverse("tasks:move", kwargs={"pk": task.pk}),
        {"container_id": col2.pk, "order": 0},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_complete_task_creates_tasklog(user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    task = Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="T3",
    )
    log = complete_task(task=task, completed_by=member)
    assert log.status == TaskStatus.COMPLETED
    task.refresh_from_db()
    assert task.status == TaskStatus.COMPLETED
    assert TaskLog.objects.filter(task=task, status=TaskStatus.COMPLETED).exists()
    assert AuditLog.objects.filter(action=AuditAction.TASK_COMPLETED).exists()


@pytest.mark.django_db
def test_column_reorder_updates_container_order(client, user):
    project, col1, col2 = _make_project_with_board(user)
    col3 = KanbanBoard.objects.create(project=project, name="Done", container_order=2)
    client.force_login(user)
    response = client.post(
        reverse("tasks:column_reorder", kwargs={"project_pk": project.pk}),
        {"order": [col3.pk, col1.pk, col2.pk]},
    )
    assert response.status_code == 204
    col1.refresh_from_db()
    col2.refresh_from_db()
    col3.refresh_from_db()
    assert (col3.container_order, col1.container_order, col2.container_order) == (0, 1, 2)


@pytest.mark.django_db
def test_column_reorder_rejects_partial_payload(client, user):
    project, col1, col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.post(
        reverse("tasks:column_reorder", kwargs={"project_pk": project.pk}),
        {"order": [col1.pk]},
    )
    assert response.status_code == 400
    col1.refresh_from_db()
    col2.refresh_from_db()
    # Order is unchanged on rejection.
    assert col1.container_order == 0
    assert col2.container_order == 1


@pytest.mark.django_db
def test_approve_completion_requires_pm(client, user, other_user):
    project, col1, _col2 = _make_project_with_board(user)
    pm_member = _primary_member(project, user)
    # Add a second user as a plain contributor.
    contrib_member = ProjectMember.objects.create(project=project)
    contrib_member.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project,
        user=other_user,
        privilege=MemberPrivileges.CONTRIBUTOR,
    )
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col1,
        container_task_order=0,
        name="T4",
    )
    log = complete_task(task=task, completed_by=pm_member)

    # Contributor cannot approve.
    client.force_login(other_user)
    url = reverse(
        "tasks:approve_log", kwargs={"pk": task.pk, "log_pk": log.pk}
    )
    response = client.post(url, {"approved": "true"})
    assert response.status_code == 403

    # PM can approve via the service layer.
    approve_completion(task_log=log, approved=True, verifier_user=user)
    log.refresh_from_db()
    assert log.approved is True
    assert AuditLog.objects.filter(action=AuditAction.TASK_APPROVED).exists()


@pytest.mark.django_db
def test_assigning_new_member_seeds_tasklog(client, user, other_user):
    project, col1, _col2 = _make_project_with_board(user)
    pm_member = _primary_member(project, user)
    extra_member = ProjectMember.objects.create(project=project)
    extra_member.users.add(other_user)
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col1,
        container_task_order=0,
        name="T5",
    )
    task.assigned_members.add(pm_member)
    TaskLog.objects.create(task=task, assigned_to=pm_member)
    assert TaskLog.objects.filter(task=task).count() == 1

    client.force_login(user)
    url = reverse("tasks:assign", kwargs={"pk": task.pk})

    # Adding a brand-new assignee seeds a TaskLog (activity entry) for them,
    # while leaving the already-assigned member's log untouched.
    response = client.post(
        url,
        {"assigned_members": [pm_member.pk, extra_member.pk]},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert TaskLog.objects.filter(task=task, assigned_to=extra_member).count() == 1
    assert TaskLog.objects.filter(task=task, assigned_to=pm_member).count() == 1
    # The response asks the page to refresh the activity timeline.
    assert response["HX-Trigger"] == "assignees-changed"

    # The activity endpoint now renders a thread for the new member.
    activity = client.get(reverse("tasks:activity", kwargs={"pk": task.pk}))
    new_log = TaskLog.objects.get(task=task, assigned_to=extra_member)
    assert f'id="task-log-{new_log.pk}"' in activity.content.decode()

    # Re-submitting with the same members does not duplicate logs.
    client.post(
        url,
        {"assigned_members": [pm_member.pk, extra_member.pk]},
        HTTP_HX_REQUEST="true",
    )
    assert TaskLog.objects.filter(task=task, assigned_to=extra_member).count() == 1


@pytest.mark.django_db
def test_assign_without_htmx_redirects_to_task(client, user, other_user):
    # When htmx isn't running, a native form submit should still seed the
    # log and reload the task page (rather than return a bare partial).
    project, col1, _col2 = _make_project_with_board(user)
    pm_member = _primary_member(project, user)
    extra_member = ProjectMember.objects.create(project=project)
    extra_member.users.add(other_user)
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col1,
        container_task_order=0,
        name="T7",
    )
    client.force_login(user)
    response = client.post(  # no HX-Request header
        reverse("tasks:assign", kwargs={"pk": task.pk}),
        {"assigned_members": [pm_member.pk, extra_member.pk]},
    )
    assert response.status_code == 302
    assert response.url == reverse(
        "tasks:detail", kwargs={"project_pk": project.pk, "pk": task.pk}
    )
    assert TaskLog.objects.filter(task=task, assigned_to=extra_member).exists()


@pytest.mark.django_db
def test_assigning_backfills_member_missing_a_tasklog(client, user, other_user):
    # An assignee that was added without a TaskLog (e.g. legacy/demo data)
    # gets one seeded the next time assignees are saved.
    project, col1, _col2 = _make_project_with_board(user)
    pm_member = _primary_member(project, user)
    no_log_member = ProjectMember.objects.create(project=project)
    no_log_member.users.add(other_user)
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col1,
        container_task_order=0,
        name="T6",
    )
    # Both are assigned, but only pm_member has an activity entry.
    task.assigned_members.add(pm_member, no_log_member)
    TaskLog.objects.create(task=task, assigned_to=pm_member)
    assert not TaskLog.objects.filter(task=task, assigned_to=no_log_member).exists()

    client.force_login(user)
    response = client.post(
        reverse("tasks:assign", kwargs={"pk": task.pk}),
        {"assigned_members": [pm_member.pk, no_log_member.pk]},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    seeded_log = TaskLog.objects.get(task=task, assigned_to=no_log_member)
    activity = client.get(reverse("tasks:activity", kwargs={"pk": task.pk}))
    assert f'id="task-log-{seeded_log.pk}"' in activity.content.decode()


@pytest.mark.django_db
def test_edit_form_seeds_assignee_logs(client, user, other_user):
    # Adding an assignee via the Task edit form should seed an activity entry,
    # just like the "Change assignees" flow does.
    project, col1, _col2 = _make_project_with_board(user)
    pm_member = _primary_member(project, user)
    extra_member = ProjectMember.objects.create(project=project)
    extra_member.users.add(other_user)
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col1,
        container_task_order=0,
        name="EditSeed",
    )
    task.assigned_members.add(pm_member)
    TaskLog.objects.create(task=task, assigned_to=pm_member)

    client.force_login(user)
    response = client.post(
        reverse("tasks:update", kwargs={"project_pk": project.pk, "pk": task.pk}),
        {
            "name": "EditSeed",
            "container": col1.pk,
            "contribution_type": "OTHER",
            "completion_mode": "INDIVIDUAL",
            "assigned_members": [pm_member.pk, extra_member.pk],
        },
    )
    assert response.status_code in (200, 302)
    assert TaskLog.objects.filter(task=task, assigned_to=extra_member).count() == 1


@pytest.mark.django_db
def test_create_form_sets_is_priority(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.post(
        reverse("tasks:create", kwargs={"project_pk": project.pk}),
        {
            "name": "Urgent task",
            "container": col1.pk,
            "contribution_type": "OTHER",
            "completion_mode": "INDIVIDUAL",
            "is_priority": "on",
        },
    )
    assert response.status_code in (200, 302)
    task = Task.objects.get(project=project, name="Urgent task")
    assert task.is_priority is True


@pytest.mark.django_db
def test_create_form_defaults_is_priority_false(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.post(
        reverse("tasks:create", kwargs={"project_pk": project.pk}),
        {
            "name": "Normal task",
            "container": col1.pk,
            "contribution_type": "OTHER",
            "completion_mode": "INDIVIDUAL",
        },
    )
    assert response.status_code in (200, 302)
    task = Task.objects.get(project=project, name="Normal task")
    assert task.is_priority is False


@pytest.mark.django_db
def test_create_form_prefills_dates_from_calendar_range_params(client, user):
    # A date range picked on the calendar view opens the create form with
    # ?start_date=&deadline= (YYYY-MM-DD) — the form should pre-fill both
    # fields, all-day style (start at 00:00, deadline at 23:59), without
    # creating anything until the user saves.
    project, _col1, _col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.get(
        reverse("tasks:create", kwargs={"project_pk": project.pk}),
        {"start_date": "2026-03-10", "deadline": "2026-03-12"},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="start_date"' in content
    assert 'value="2026-03-10T00:00"' in content
    assert 'value="2026-03-12T23:59"' in content
    assert not Task.objects.filter(project=project).exists()


@pytest.mark.django_db
def test_create_form_ignores_invalid_calendar_range_params(client, user):
    project, _col1, _col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.get(
        reverse("tasks:create", kwargs={"project_pk": project.pk}),
        {"start_date": "not-a-date", "deadline": ""},
    )
    assert response.status_code == 200
    assert 'value="not-a-date"' not in response.content.decode()


def test_task_form_datetime_fields_render_html5_local_format():
    d = timezone.make_aware(datetime(2026, 3, 10, 0, 0))
    form = TaskForm(project=None, initial={"deadline": d, "start_date": d})
    rendered = str(form["deadline"])
    assert 'value="2026-03-10T00:00"' in rendered
    assert "2026-03-10 00:00:00" not in rendered


@pytest.mark.django_db
def test_priority_task_shows_flag_on_board_card(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    task = Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Flagged",
        is_priority=True,
    )
    client.force_login(user)
    response = client.get(reverse("tasks:board", kwargs={"project_pk": project.pk}))
    assert response.status_code == 200
    assert "High priority" in response.content.decode()


# ---------------------------------------------------------------------------
# Authorization: only Project Leads may create/edit/delete/move tasks etc.
# ---------------------------------------------------------------------------


def _add_contributor(project, user):
    """Add ``user`` as a plain Member (CONTRIBUTOR, not a Lead)."""
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    ProjectPrivilege.objects.create(
        project=project, user=user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    return member


@pytest.mark.django_db
def test_member_cannot_mutate_tasks(client, user, other_user):
    project, col1, col2 = _make_project_with_board(user)
    lead_member = _primary_member(project, user)
    _add_contributor(project, other_user)
    task = Task.objects.create(
        project=project,
        created_by=lead_member,
        container=col1,
        container_task_order=0,
        name="Locked",
    )
    milestone_pks = {"project_pk": project.pk}
    client.force_login(other_user)

    # Each of these mutating endpoints must be forbidden for a plain member.
    forbidden = [
        ("post", reverse("tasks:create", kwargs={"project_pk": project.pk}), {}),
        ("post", reverse("tasks:update", kwargs={"project_pk": project.pk, "pk": task.pk}), {}),
        ("post", reverse("tasks:delete", kwargs={"project_pk": project.pk, "pk": task.pk}), {}),
        ("post", reverse("tasks:assign", kwargs={"pk": task.pk}), {}),
        ("post", reverse("tasks:move", kwargs={"pk": task.pk}), {"container_id": col2.pk, "order": 0}),
        ("post", reverse("tasks:change_column", kwargs={"pk": task.pk}), {"container_id": col2.pk}),
        ("post", reverse("tasks:reopen", kwargs={"pk": task.pk}), {}),
        ("post", reverse("tasks:set_milestone", kwargs={"pk": task.pk}), {}),
        ("post", reverse("tasks:column_create", kwargs=milestone_pks), {"name": "X"}),
        ("post", reverse("tasks:milestone_create", kwargs=milestone_pks), {"name": "M"}),
    ]
    for method, url, data in forbidden:
        resp = getattr(client, method)(url, data)
        assert resp.status_code == 403, f"{url} returned {resp.status_code}, expected 403"

    # The task must not have moved.
    task.refresh_from_db()
    assert task.container_id == col1.pk


@pytest.mark.django_db
def test_member_can_view_comment_and_complete(client, user, other_user):
    project, col1, _col2 = _make_project_with_board(user)
    lead_member = _primary_member(project, user)
    contrib_member = _add_contributor(project, other_user)
    task = Task.objects.create(
        project=project,
        created_by=lead_member,
        container=col1,
        container_task_order=0,
        name="Viewable",
    )
    task.assigned_members.add(contrib_member)
    log = TaskLog.objects.create(task=task, assigned_to=contrib_member)
    client.force_login(other_user)

    # View the task detail.
    detail = client.get(
        reverse("tasks:detail", kwargs={"project_pk": project.pk, "pk": task.pk})
    )
    assert detail.status_code == 200

    # Post a comment.
    comment = client.post(
        reverse("comments:create", kwargs={"task_pk": task.pk, "log_pk": log.pk}),
        {"content": "Looks good"},
    )
    assert comment.status_code in (200, 201)
    assert task.task_logs.get(pk=log.pk).comments.filter(content="Looks good").exists()

    # Mark the task done (members are allowed to).
    done = client.post(reverse("tasks:complete", kwargs={"pk": task.pk}))
    assert done.status_code in (200, 302)
    assert TaskLog.objects.filter(task=task, status=TaskStatus.COMPLETED).exists()


# ---------------------------------------------------------------------------
# TEAM vs INDIVIDUAL task completion
# ---------------------------------------------------------------------------


def _two_assignee_task(user, other_user, *, mode):
    from apps.core.enums import CompletedAs

    project, col1, _col2 = _make_project_with_board(user)
    lead = _primary_member(project, user)
    contrib = ProjectMember.objects.create(project=project)
    contrib.users.add(other_user)
    ProjectPrivilege.objects.create(
        project=project, user=other_user, privilege=MemberPrivileges.CONTRIBUTOR
    )
    task = Task.objects.create(
        project=project,
        created_by=lead,
        container=col1,
        container_task_order=0,
        name="Moded",
        completion_mode=mode,
    )
    task.assigned_members.add(lead, contrib)
    TaskLog.objects.create(task=task, assigned_to=lead)
    TaskLog.objects.create(task=task, assigned_to=contrib)
    return project, task, lead, contrib


@pytest.mark.django_db
def test_individual_mode_close_blocked_until_all_done(client, user, other_user):
    from apps.core.enums import CompletedAs

    project, task, lead, contrib = _two_assignee_task(
        user, other_user, mode=CompletedAs.INDIVIDUAL
    )

    # A member marks only their own part done.
    client.force_login(other_user)
    client.post(reverse("tasks:complete", kwargs={"pk": task.pk}))
    task.refresh_from_db()
    assert task.status == TaskStatus.NOT_COMPLETED  # not all done yet
    assert task.assignees_done_count == 1

    # Lead cannot close while someone is still outstanding.
    client.force_login(user)
    blocked = client.post(reverse("tasks:close", kwargs={"pk": task.pk}))
    assert blocked.status_code == 400
    task.refresh_from_db()
    assert task.status == TaskStatus.NOT_COMPLETED

    # The remaining assignee (the lead) marks their part; now close succeeds.
    from apps.tasks.services import mark_own_done

    mark_own_done(task=task, member=lead)
    ok = client.post(reverse("tasks:close", kwargs={"pk": task.pk}))
    assert ok.status_code in (200, 302)
    task.refresh_from_db()
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.django_db
def test_team_mode_lead_closes_for_everyone(client, user, other_user):
    from apps.core.enums import CompletedAs

    project, task, lead, contrib = _two_assignee_task(
        user, other_user, mode=CompletedAs.TEAM
    )

    # A member cannot self-mark in TEAM mode.
    client.force_login(other_user)
    resp = client.post(reverse("tasks:complete", kwargs={"pk": task.pk}))
    assert resp.status_code == 400

    # A member cannot close (Lead-only).
    assert client.post(reverse("tasks:close", kwargs={"pk": task.pk})).status_code == 403

    # The Lead closes once for everyone.
    client.force_login(user)
    ok = client.post(reverse("tasks:close", kwargs={"pk": task.pk}))
    assert ok.status_code in (200, 302)
    task.refresh_from_db()
    assert task.status == TaskStatus.COMPLETED
    # Every assignee ends up done.
    assert task.all_assignees_done


@pytest.mark.django_db
def test_board_calendar_only_shows_tasks_with_calendar_fields(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Dated",
        deadline=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
    )
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=1,
        name="Undated",
    )
    client.force_login(user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": "month", "date": "2026-03-01"},
    )
    assert response.status_code == 200
    assert b"Dated" in response.content
    assert b"Undated" not in response.content


@pytest.mark.django_db
def test_board_calendar_requires_member_access(client, user, other_user):
    project, _col1, _col2 = _make_project_with_board(user)
    client.force_login(other_user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk})
    )
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("view", ["month", "week", "day"])
def test_board_calendar_renders_task_name_at_each_granularity(client, user, view):
    # Year view only renders a dot indicator per day (no room for task names),
    # so it's covered separately below.
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Granularity task",
        deadline=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
    )
    client.force_login(user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": view, "date": "2026-03-15"},
    )
    assert response.status_code == 200
    assert b"Granularity task" in response.content


@pytest.mark.django_db
def test_board_calendar_year_view_renders(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Granularity task",
        deadline=datetime(2026, 3, 15, tzinfo=dt_timezone.utc),
    )
    client.force_login(user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": "year", "date": "2026-03-15"},
    )
    assert response.status_code == 200
    assert b"March" in response.content


@pytest.mark.django_db
def test_board_calendar_unknown_view_falls_back_to_month(client, user):
    project, _col1, _col2 = _make_project_with_board(user)
    client.force_login(user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": "century"},
    )
    assert response.status_code == 200


class _FakeTask:
    def __init__(self, name, deadline=None, start_date=None, is_priority=False):
        self.name = name
        self.deadline = deadline
        self.start_date = start_date
        self.is_priority = is_priority


def test_build_month_grid_buckets_task_on_its_deadline_date():
    task = _FakeTask("A", deadline=datetime(2026, 3, 15, tzinfo=dt_timezone.utc))
    weeks = build_month_grid(2026, 3, [task])
    matches = [
        day
        for week in weeks
        for day in week["days"]
        if day.date.day == 15 and day.in_current_month
    ]
    assert len(matches) == 1
    assert matches[0].tasks == [task]


def test_build_month_grid_falls_back_to_start_date():
    task = _FakeTask("B", start_date=datetime(2026, 3, 3, tzinfo=dt_timezone.utc))
    weeks = build_month_grid(2026, 3, [task])
    matches = [day for week in weeks for day in week["days"] if day.date.day == 3]
    assert matches[0].tasks == [task]


def test_clamp_month_rolls_into_next_year():
    assert clamp_month(2026, 13) == (2027, 1)
    assert clamp_month(2026, 0) == (2025, 12)


def test_build_year_grid_places_task_in_its_month():
    task = _FakeTask("Yearly", deadline=datetime(2026, 7, 4, tzinfo=dt_timezone.utc))
    months = build_year_grid(2026, [task])
    assert len(months) == 12
    july = next(m for m in months if m["month"] == 7)
    matches = [
        day for week in july["weeks"] for day in week
        if day.date.day == 4 and day.in_current_month
    ]
    assert matches[0].tasks == [task]
    # No other month's *own* days (excluding leading/trailing overflow days
    # from adjacent months, which may legitimately echo the same date) carry
    # the task.
    other_months = [m for m in months if m["month"] != 7]
    assert all(
        not day.tasks
        for m in other_months
        for week in m["weeks"]
        for day in week
        if day.in_current_month
    )


def test_build_week_grid_spans_monday_to_sunday_and_includes_anchor():
    task = _FakeTask("Weekly", deadline=datetime(2026, 3, 18, tzinfo=dt_timezone.utc))
    # 2026-03-18 is a Wednesday.
    week = build_week_grid(date(2026, 3, 18), [task])
    days = week["days"]
    assert len(days) == 7
    assert days[0].date == date(2026, 3, 16)  # Monday
    assert days[-1].date == date(2026, 3, 22)  # Sunday
    matches = [day for day in days if day.date == date(2026, 3, 18)]
    assert matches[0].tasks == [task]
    assert week["lanes"] == []  # not a range: no bars


def test_build_day_tasks_returns_only_that_days_tasks():
    on_day = _FakeTask("OnDay", deadline=datetime(2026, 3, 18, tzinfo=dt_timezone.utc))
    other_day = _FakeTask("OtherDay", deadline=datetime(2026, 3, 19, tzinfo=dt_timezone.utc))
    items = build_day_tasks(date(2026, 3, 18), [on_day, other_day])
    assert [item.task for item in items] == [on_day]
    assert items[0].is_range is False


def test_parse_view_date_falls_back_to_today_on_bad_input():
    assert parse_view_date("not-a-date") == timezone.localdate()
    assert parse_view_date(None) == timezone.localdate()
    assert parse_view_date("2026-03-18") == date(2026, 3, 18)


def test_shift_date_per_granularity():
    anchor = date(2026, 3, 18)
    assert shift_date("day", anchor, 1) == date(2026, 3, 19)
    assert shift_date("week", anchor, 1) == date(2026, 3, 25)
    assert shift_date("month", anchor, 1) == date(2026, 4, 1)
    assert shift_date("year", anchor, 1) == date(2027, 1, 1)
    assert shift_date("month", date(2026, 12, 1), 1) == date(2027, 1, 1)


@pytest.mark.django_db
def test_board_full_page_renders_calendar_section(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Full page dated task",
        deadline=datetime.now(dt_timezone.utc),
    )
    client.force_login(user)
    response = client.get(reverse("tasks:board", kwargs={"project_pk": project.pk}))
    assert response.status_code == 200
    assert b"$store.board.setView('calendar')" in response.content
    assert b"calendar-view-container" in response.content
    assert b"Full page dated task" in response.content


def test_task_range_returns_none_for_single_day_task():
    same_day = _FakeTask(
        "Same day",
        start_date=datetime(2026, 3, 10, 9, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 10, 17, tzinfo=dt_timezone.utc),
    )
    assert task_range(same_day) is None
    assert task_range(_FakeTask("Deadline only", deadline=datetime(2026, 3, 10, tzinfo=dt_timezone.utc))) is None
    assert task_range(_FakeTask("Neither")) is None


def test_task_range_returns_sorted_span_for_multi_day_task():
    task = _FakeTask(
        "Spans days",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 14, tzinfo=dt_timezone.utc),
    )
    assert task_range(task) == (date(2026, 3, 10), date(2026, 3, 14))


def test_task_range_swaps_reversed_dates():
    # deadline set before start_date — still treated as a valid range, sorted.
    task = _FakeTask(
        "Reversed",
        start_date=datetime(2026, 3, 14, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
    )
    assert task_range(task) == (date(2026, 3, 10), date(2026, 3, 14))


def test_bucket_tasks_by_day_covers_every_day_in_a_range():
    task = _FakeTask(
        "Ranged",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    by_day = bucket_tasks_by_day([task])
    assert by_day[date(2026, 3, 10)] == [task]
    assert by_day[date(2026, 3, 11)] == [task]
    assert by_day[date(2026, 3, 12)] == [task]
    assert date(2026, 3, 13) not in by_day


def test_build_month_grid_renders_ranged_task_as_a_bar_not_a_pill():
    task = _FakeTask(
        "Ranged",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    weeks = build_month_grid(2026, 3, [task])
    # The task never appears in any day cell's pill list...
    assert all(task not in day.tasks for week in weeks for day in week["days"])
    # ...it appears exactly once, as a lane bar, in the week it falls in.
    weeks_with_bars = [w for w in weeks if w["lanes"]]
    assert len(weeks_with_bars) == 1
    lanes = weeks_with_bars[0]["lanes"]
    assert len(lanes) == 1
    bar = lanes[0][0]
    assert bar["task"] is task
    assert bar["is_range_start"] is True
    assert bar["is_range_end"] is True
    # 2026-03-10 is a Tuesday (col 2), 2026-03-12 is a Thursday (col 4).
    assert bar["start_col"] == 2
    assert bar["end_col"] == 4


def test_build_month_grid_splits_range_bar_across_week_boundary():
    # 2026-03-16 (Mon) through 2026-03-20 (Fri) is a single week, so pick a
    # range that crosses into the next week to exercise clipping.
    task = _FakeTask(
        "Crosses weeks",
        start_date=datetime(2026, 3, 20, tzinfo=dt_timezone.utc),  # Friday
        deadline=datetime(2026, 3, 23, tzinfo=dt_timezone.utc),  # Monday (next week)
    )
    weeks = build_month_grid(2026, 3, [task])
    weeks_with_bars = [w for w in weeks if w["lanes"]]
    assert len(weeks_with_bars) == 2

    first_bar = weeks_with_bars[0]["lanes"][0][0]
    assert first_bar["is_range_start"] is True
    assert first_bar["is_range_end"] is False
    assert first_bar["end_col"] == 7  # clipped to Sunday

    second_bar = weeks_with_bars[1]["lanes"][0][0]
    assert second_bar["is_range_start"] is False
    assert second_bar["is_range_end"] is True
    assert second_bar["start_col"] == 1  # clipped to Monday


def test_build_month_grid_stacks_overlapping_ranges_into_separate_lanes():
    task_a = _FakeTask(
        "A",
        start_date=datetime(2026, 3, 9, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 11, tzinfo=dt_timezone.utc),
    )
    task_b = _FakeTask(
        "B",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    weeks = build_month_grid(2026, 3, [task_a, task_b])
    lanes = next(w["lanes"] for w in weeks if w["lanes"])
    assert len(lanes) == 2  # overlapping columns forced into separate lanes
    assert {bar["task"] for lane in lanes for bar in lane} == {task_a, task_b}


def test_build_month_grid_shares_a_lane_for_non_overlapping_ranges():
    task_a = _FakeTask(
        "A",
        start_date=datetime(2026, 3, 2, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 3, tzinfo=dt_timezone.utc),
    )
    task_b = _FakeTask(
        "B",
        start_date=datetime(2026, 3, 5, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 6, tzinfo=dt_timezone.utc),
    )
    weeks = build_month_grid(2026, 3, [task_a, task_b])
    lanes = next(w["lanes"] for w in weeks if w["lanes"])
    assert len(lanes) == 1  # no column overlap: share lane 0
    assert len(lanes[0]) == 2


def test_build_week_grid_renders_ranged_task_as_a_bar():
    task = _FakeTask(
        "Ranged",
        start_date=datetime(2026, 3, 17, tzinfo=dt_timezone.utc),  # Tuesday
        deadline=datetime(2026, 3, 19, tzinfo=dt_timezone.utc),  # Thursday
    )
    week = build_week_grid(date(2026, 3, 18), [task])
    assert all(task not in day.tasks for day in week["days"])
    assert len(week["lanes"]) == 1
    bar = week["lanes"][0][0]
    assert bar["start_col"] == 2
    assert bar["end_col"] == 4


def test_build_day_tasks_marks_ranged_tasks_with_their_span():
    task = _FakeTask(
        "Ranged",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    items = build_day_tasks(date(2026, 3, 11), [task])
    assert len(items) == 1
    assert items[0].is_range is True
    assert items[0].range_start == date(2026, 3, 10)
    assert items[0].range_end == date(2026, 3, 12)


@pytest.mark.django_db
def test_board_calendar_month_view_renders_range_bar_markup(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project,
        created_by=member,
        container=col1,
        container_task_order=0,
        name="Ranged task",
        start_date=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    client.force_login(user)
    response = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": "month", "date": "2026-03-01"},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "Ranged task" in content
    assert "col-start-2" in content
    assert "col-end-5" in content


# ---------------------------------------------------------------------------
# List view: search / filter / sort
# ---------------------------------------------------------------------------


def _list_url(project):
    return reverse("tasks:board_list", kwargs={"project_pk": project.pk})


def _row_order(content: str, names) -> list[str]:
    """Names in the order they appear in the rendered rows."""
    return sorted(
        (name for name in names if name in content),
        key=content.index,
    )


@pytest.mark.django_db
def test_board_list_search_matches_name_description_and_assignee(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    by_name = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Widget wiring",
    )
    by_desc = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=1, name="Unrelated", description="mentions widget here",
    )
    by_assignee = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=2, name="Assigned elsewhere",
    )
    by_assignee.assigned_members.add(member)
    miss = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=3, name="Nothing to see",
    )

    client.force_login(user)
    content = client.get(_list_url(project), {"q": "widget"}).content.decode()
    assert by_name.name in content
    assert by_desc.name in content
    assert miss.name not in content

    # The member's display name falls back to the user's name, "Alice".
    content = client.get(_list_url(project), {"q": "alice"}).content.decode()
    assert by_assignee.name in content
    assert miss.name not in content


@pytest.mark.django_db
def test_board_list_filters_by_column_completion_and_priority(client, user):
    project, col1, col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    plain = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Plain task",
    )
    done = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=1, name="Finished task", status=TaskStatus.COMPLETED,
    )
    urgent = Task.objects.create(
        project=project, created_by=member, container=col2,
        container_task_order=0, name="Urgent task", is_priority=True,
    )
    client.force_login(user)

    content = client.get(_list_url(project), {"col": col2.pk}).content.decode()
    assert urgent.name in content
    assert plain.name not in content
    # Empty columns are dropped while filtering rather than left as dead sections.
    assert f'data-list-column-id="{col2.pk}"' in content
    assert f'data-list-column-id="{col1.pk}"' not in content

    content = client.get(_list_url(project), {"done": "complete"}).content.decode()
    assert done.name in content
    assert plain.name not in content

    content = client.get(_list_url(project), {"done": "not_complete"}).content.decode()
    assert plain.name in content
    assert done.name not in content

    content = client.get(_list_url(project), {"priority": "high"}).content.decode()
    assert urgent.name in content
    assert plain.name not in content


@pytest.mark.django_db
def test_board_list_filters_by_assignee_and_unassigned(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    mine = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Mine",
    )
    mine.assigned_members.add(member)
    nobodys = Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=1, name="Unclaimed",
    )
    client.force_login(user)

    content = client.get(_list_url(project), {"assignee": member.pk}).content.decode()
    assert mine.name in content
    assert nobodys.name not in content

    content = client.get(_list_url(project), {"assignee": "none"}).content.decode()
    assert nobodys.name in content
    assert mine.name not in content


@pytest.mark.django_db
def test_board_list_sort_orders_rows_and_puts_undated_last(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    names = ["Beta", "Alpha", "Gamma"]
    for order, name in enumerate(names):
        Task.objects.create(
            project=project, created_by=member, container=col1,
            container_task_order=order, name=name,
            deadline=(
                datetime(2026, 3, 5, tzinfo=dt_timezone.utc) if name == "Beta"
                else datetime(2026, 3, 1, tzinfo=dt_timezone.utc) if name == "Alpha"
                else None
            ),
        )
    client.force_login(user)

    content = client.get(_list_url(project)).content.decode()
    assert _row_order(content, names) == ["Beta", "Alpha", "Gamma"]

    content = client.get(_list_url(project), {"sort": "name_asc"}).content.decode()
    assert _row_order(content, names) == ["Alpha", "Beta", "Gamma"]

    content = client.get(_list_url(project), {"sort": "name_desc"}).content.decode()
    assert _row_order(content, names) == ["Gamma", "Beta", "Alpha"]

    # Undated tasks sort last in both directions, never ahead of dated ones.
    content = client.get(_list_url(project), {"sort": "deadline_asc"}).content.decode()
    assert _row_order(content, names) == ["Alpha", "Beta", "Gamma"]

    content = client.get(_list_url(project), {"sort": "deadline_desc"}).content.decode()
    assert _row_order(content, names) == ["Beta", "Alpha", "Gamma"]


@pytest.mark.django_db
def test_board_list_renders_rows_without_a_control_bar(client, user):
    """The search / filter / sort bar is hidden; the rows still render, and
    ?results=1 still returns them on their own."""
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Only task",
    )
    client.force_login(user)

    full = client.get(_list_url(project)).content.decode()
    assert "Only task" in full
    assert 'id="list-results"' in full
    assert 'id="list-filters"' not in full
    assert 'name="q"' not in full
    assert 'name="sort"' not in full

    rows = client.get(_list_url(project), {"results": "1"}).content.decode()
    assert "Only task" in rows
    assert 'id="list-results"' not in rows


@pytest.mark.django_db
def test_board_list_drag_handle_only_shows_in_unfiltered_board_order(client, pm_user):
    project, col1, _col2 = _make_project_with_board(pm_user)
    member = _primary_member(project, pm_user)
    Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Draggable",
    )
    client.force_login(pm_user)

    assert "task-drag-handle" in client.get(_list_url(project)).content.decode()

    sorted_rows = client.get(_list_url(project), {"sort": "name_asc"}).content.decode()
    assert "task-drag-handle" not in sorted_rows

    filtered = client.get(_list_url(project), {"q": "Drag"}).content.decode()
    assert "task-drag-handle" not in filtered


@pytest.mark.django_db
def test_board_list_reports_no_matches_and_ignores_unknown_params(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Real task",
    )
    client.force_login(user)

    content = client.get(_list_url(project), {"q": "nothing matches this"}).content.decode()
    assert "No matching tasks" in content
    assert "Real task" not in content

    # Junk filter/sort values fall back to "no filter" rather than 500ing.
    content = client.get(
        _list_url(project),
        {"sort": "bogus", "done": "bogus", "priority": "bogus",
         "col": "9999", "assignee": "9999"},
    ).content.decode()
    assert "Real task" in content
    assert "No matching tasks" not in content


@pytest.mark.django_db
def test_board_page_shows_manage_columns_for_every_view(client, pm_user):
    """The button used to be Kanban-only; columns are edited from any view."""
    project, _col1, _col2 = _make_project_with_board(pm_user)
    client.force_login(pm_user)
    response = client.get(reverse("tasks:board", kwargs={"project_pk": project.pk}))
    content = response.content.decode()
    assert "Manage columns" in content
    # No x-show gate on the button itself (the view switcher still uses one).
    button = content[content.rindex("<button", 0, content.index("Manage columns")):]
    assert "x-show" not in button[: button.index("</button>")]


# ---------------------------------------------------------------------------
# Shared task date label
# ---------------------------------------------------------------------------


def _label(start=None, end=None, today=date(2026, 6, 1)):
    return task_date_label(_FakeTask("T", start_date=start, deadline=end), today=today)


def test_task_date_label_shows_a_span_when_both_dates_are_set():
    label = _label(
        start=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
        end=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    # The year is dropped in the current year, and carried once on the end date.
    assert label["short"] == "Mar 10 – Mar 12"
    assert label["full"] == "Mar 10, 2026 – Mar 12, 2026"


def test_task_date_label_carries_the_year_outside_the_current_one():
    label = _label(
        start=datetime(2027, 3, 10, tzinfo=dt_timezone.utc),
        end=datetime(2027, 3, 12, tzinfo=dt_timezone.utc),
    )
    assert label["short"] == "Mar 10 – Mar 12, 2027"

    # A span across a year boundary spells out both years.
    label = _label(
        start=datetime(2026, 12, 28, tzinfo=dt_timezone.utc),
        end=datetime(2027, 1, 3, tzinfo=dt_timezone.utc),
    )
    assert label["short"] == "Dec 28, 2026 – Jan 3, 2027"


def test_task_date_label_names_a_lone_date_so_it_is_never_ambiguous():
    assert _label(end=datetime(2026, 3, 12, tzinfo=dt_timezone.utc))["short"] == "Due Mar 12"
    assert _label(start=datetime(2026, 3, 10, tzinfo=dt_timezone.utc))["short"] == "Starts Mar 10"
    # Both dates on the same day is a due date, not a zero-length span.
    same = datetime(2026, 3, 12, tzinfo=dt_timezone.utc)
    assert _label(start=same, end=same)["short"] == "Due Mar 12"


def test_task_date_label_is_empty_without_dates():
    assert _label() == {"short": "", "full": ""}


def test_task_date_label_orders_a_reversed_span():
    label = _label(
        start=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
        end=datetime(2026, 3, 10, tzinfo=dt_timezone.utc),
    )
    assert label["short"] == "Mar 10 – Mar 12"


@pytest.mark.django_db
def test_kanban_list_and_calendar_render_the_same_date_span(client, user):
    """One task, three views, one date string."""
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project, created_by=member, container=col1, container_task_order=0,
        name="Ranged task",
        start_date=datetime(2026, 3, 10, 9, 0, tzinfo=dt_timezone.utc),
        deadline=datetime(2026, 3, 12, 17, 0, tzinfo=dt_timezone.utc),
    )
    client.force_login(user)
    span = task_date_label(Task.objects.get(name="Ranged task"))["short"]
    assert "–" in span

    kanban = client.get(
        reverse("tasks:board_kanban", kwargs={"project_pk": project.pk})
    ).content.decode()
    listing = client.get(
        reverse("tasks:board_list", kwargs={"project_pk": project.pk})
    ).content.decode()
    calendar_day = client.get(
        reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
        {"view": "day", "date": "2026-03-11"},
    ).content.decode()

    for view_name, content in (
        ("kanban", kanban), ("list", listing), ("calendar", calendar_day),
    ):
        assert span in content, f"{view_name} view is missing the shared date span"
        # The old deadline-only rendering is gone from all three.
        assert "Mar 12, 2026<" not in content


@pytest.mark.django_db
def test_kanban_card_has_no_status_pill_but_the_column_heading_names_it(client, user):
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project, created_by=member, container=col1,
        container_task_order=0, name="Carded",
    )
    client.force_login(user)
    content = client.get(
        reverse("tasks:board_kanban", kwargs={"project_pk": project.pk})
    ).content.decode()

    assert "Carded" in content
    # The column header still carries the status; the card no longer repeats it.
    card = content[content.index('id="task-card-'):]
    card = card[: card.index("</li>")]
    assert "Carded" in card
    assert col1.name not in card
    assert "rounded-full text-xs font-medium" not in card


@pytest.mark.django_db
def test_task_descriptions_stay_out_of_the_kanban_list_and_day_views(client, user):
    """Descriptions live in the side pane; the browsing views show names only.
    Search still reads the description server-side."""
    project, col1, _col2 = _make_project_with_board(user)
    member = _primary_member(project, user)
    Task.objects.create(
        project=project, created_by=member, container=col1, container_task_order=0,
        name="Documented", description="a description nobody should see here",
        deadline=datetime(2026, 3, 12, tzinfo=dt_timezone.utc),
    )
    client.force_login(user)

    views = {
        "kanban": (reverse("tasks:board_kanban", kwargs={"project_pk": project.pk}), {}),
        "list": (reverse("tasks:board_list", kwargs={"project_pk": project.pk}), {}),
        "calendar day": (
            reverse("tasks:board_calendar", kwargs={"project_pk": project.pk}),
            {"view": "day", "date": "2026-03-12"},
        ),
    }
    for name, (url, params) in views.items():
        content = client.get(url, params).content.decode()
        assert "Documented" in content, f"{name} view lost the task name"
        assert "nobody should see here" not in content, f"{name} view still shows the description"

    # The pane is where the description belongs.
    pane = client.get(
        reverse("tasks:detail_pane", kwargs={"project_pk": project.pk, "pk": Task.objects.get().pk})
    ).content.decode()
    assert "nobody should see here" in pane

    # ...and searching on it still works even though it is not displayed.
    hit = client.get(views["list"][0], {"q": "nobody should see"}).content.decode()
    assert "Documented" in hit
