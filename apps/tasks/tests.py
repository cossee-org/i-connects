"""Smoke tests for the tasks app."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.core.enums import MemberPrivileges, ProjectStatus, TaskStatus
from apps.projects.models import ProjectMember, ProjectPrivilege
from apps.projects.services import create_project_with_owner
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
