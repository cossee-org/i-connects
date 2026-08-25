"""Tests for the comments app."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.comments.models import Comment
from apps.core.enums import ProjectStatus
from apps.projects.models import ProjectMember
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task


@pytest.fixture
def commentable(db, user):
    """A task the fixture user can comment on, plus their membership."""
    project = create_project_with_owner(
        user=user,
        form_data={"name": "P", "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    member = ProjectMember.objects.filter(project=project, users=user).first()
    column = KanbanBoard.objects.create(
        project=project, name="Todo", container_order=0
    )
    task = Task.objects.create(
        project=project, created_by=member, container=column,
        container_task_order=0, name="T",
    )
    return project, task, member


@pytest.mark.django_db
def test_a_new_comment_is_not_edited(commentable):
    """auto_now_add and auto_now land microseconds apart, not equal.

    The pane used to compare them for equality and so marked every comment
    "(edited)" the moment it was posted.
    """
    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="hello")
    comment.refresh_from_db()

    assert comment.updated_at != comment.created_at, "no gap left to guard against"
    assert (comment.updated_at - comment.created_at) < timedelta(seconds=1)
    assert comment.is_edited is False


@pytest.mark.django_db
def test_an_edited_comment_is_edited(commentable):
    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="hello")
    # A later request: auto_now moves updated_at on, created_at stays put.
    Comment.objects.filter(pk=comment.pk).update(
        updated_at=comment.created_at + timedelta(minutes=5)
    )
    comment.refresh_from_db()

    assert comment.is_edited is True


@pytest.mark.django_db
def test_an_unsaved_comment_is_not_edited(commentable):
    """No timestamps yet, so nothing to compare — and no crash."""
    _project, task, member = commentable
    assert Comment(task=task, author=member, content="draft").is_edited is False


@pytest.mark.django_db
def test_posting_a_comment_renders_no_edited_marker(client, commentable, user):
    """End to end: the marker is absent on a comment that was just posted."""
    _project, task, _member = commentable
    client.force_login(user)

    response = client.post(
        reverse("comments:create", kwargs={"task_pk": task.pk}),
        {"content": "First!"},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert "(edited)" not in response.content.decode()


@pytest.mark.django_db
def test_editing_a_comment_renders_the_marker(client, commentable, user):
    """... and present once the author actually changes it."""
    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="typo")
    # Backdate the post so the edit lands outside the insert's tolerance,
    # the way a real edit minutes later would.
    Comment.objects.filter(pk=comment.pk).update(
        created_at=timezone.now() - timedelta(minutes=5)
    )
    client.force_login(user)

    response = client.post(
        reverse("comments:edit", kwargs={"pk": comment.pk}),
        {"content": "fixed"},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "(edited)" in body
    assert "fixed" in body


@pytest.mark.django_db
def test_the_marker_says_when_it_was_edited(commentable, client, user):
    """The date is in the title, so hovering answers "when?"."""
    from django.template.loader import render_to_string

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")
    Comment.objects.filter(pk=comment.pk).update(
        updated_at=comment.created_at + timedelta(days=1)
    )
    comment.refresh_from_db()

    html = render_to_string(
        "comments/comment_item.html", {"comment": comment, "task": task}
    )
    assert f'title="Edited {comment.updated_at.strftime("%b %-d, %Y %H:%M")}"' in html
