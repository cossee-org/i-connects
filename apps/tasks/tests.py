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
    build_day_tasks,
    build_month_grid,
    build_week_grid,
    build_year_grid,
    clamp_month,
    parse_view_date,
    shift_date,
)
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
        day for week in weeks for day in week if day.date.day == 15 and day.in_current_month
    ]
    assert len(matches) == 1
    assert matches[0].tasks == [task]


def test_build_month_grid_falls_back_to_start_date():
    task = _FakeTask("B", start_date=datetime(2026, 3, 3, tzinfo=dt_timezone.utc))
    weeks = build_month_grid(2026, 3, [task])
    matches = [day for week in weeks for day in week if day.date.day == 3]
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
    assert len(week) == 7
    assert week[0].date == date(2026, 3, 16)  # Monday
    assert week[-1].date == date(2026, 3, 22)  # Sunday
    matches = [day for day in week if day.date == date(2026, 3, 18)]
    assert matches[0].tasks == [task]


def test_build_day_tasks_returns_only_that_days_tasks():
    on_day = _FakeTask("OnDay", deadline=datetime(2026, 3, 18, tzinfo=dt_timezone.utc))
    other_day = _FakeTask("OtherDay", deadline=datetime(2026, 3, 19, tzinfo=dt_timezone.utc))
    tasks = build_day_tasks(date(2026, 3, 18), [on_day, other_day])
    assert tasks == [on_day]


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
    assert b"View as calendar" in response.content
    assert b"calendar-view-container" in response.content
    assert b"Full page dated task" in response.content
