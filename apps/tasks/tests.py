"""Smoke tests for the tasks app."""

from __future__ import annotations

import pytest
from django.urls import reverse

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
    approve_completion(task_log=log, approved=True)
    log.refresh_from_db()
    assert log.approved is True
