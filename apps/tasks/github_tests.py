"""Tests for the GitHub issue / PR linking on tasks."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.enums import ProjectStatus
from apps.projects.models import ProjectMember
from apps.projects.services import create_project_with_owner
from apps.tasks.github_links import parse_github_url
from apps.tasks.models import KanbanBoard, Task


def _make_project_with_board(owner, *, name="P"):
    project = create_project_with_owner(
        user=owner,
        form_data={"name": name, "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    col = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    return project, col


def test_parse_github_url_issue():
    result = parse_github_url("https://github.com/octo/hello/issues/42")
    assert result == {
        "owner": "octo",
        "repo": "hello",
        "kind": "issues",
        "number": 42,
        "short": "octo/hello#42",
    }


def test_parse_github_url_pull_request():
    result = parse_github_url("https://github.com/octo/hello/pull/7")
    assert result is not None
    assert result["kind"] == "pull"
    assert result["number"] == 7
    assert result["short"] == "octo/hello#7"


def test_parse_github_url_non_github_returns_none():
    assert parse_github_url("https://gitlab.com/octo/hello/issues/42") is None
    assert parse_github_url("https://example.com/octo/hello/issues/42") is None


def test_parse_github_url_malformed_returns_none():
    assert parse_github_url("") is None
    assert parse_github_url("not a url") is None
    assert parse_github_url("https://github.com/octo/hello") is None
    assert parse_github_url("https://github.com/octo/hello/issues/abc") is None


@pytest.mark.django_db
def test_task_detail_renders_github_link(client, user):
    project, col = _make_project_with_board(user)
    member = ProjectMember.objects.filter(project=project, users=user).first()
    task = Task.objects.create(
        project=project,
        created_by=member,
        container=col,
        container_task_order=0,
        name="Analysis code",
        github_url="https://github.com/octo/hello/issues/42",
    )
    client.force_login(user)

    response = client.get(
        reverse("tasks:detail", kwargs={"project_pk": project.pk, "pk": task.pk})
    )

    assert response.status_code == 200
    assert b"octo/hello#42" in response.content
