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
    # Twelve-hour, and carrying the instant for the browser to restate locally.
    assert f'title="Edited {comment.updated_at.strftime("%b %-d, %Y %-I:%M %p")}"' in html
    assert f'data-epoch="{int(comment.updated_at.timestamp())}"' in html


@pytest.mark.django_db
def test_comment_actions_are_text_only(commentable):
    """Edit and Delete are labels, no glyphs."""
    from django.template.loader import render_to_string

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")

    own = render_to_string(
        "comments/comment_item.html",
        {"comment": comment, "task": task, "project_member": member},
    )
    assert "<svg" not in own
    for label in ("Edit", "Delete"):
        assert f'aria-label="{label} comment"' in own
        assert f">{label}</button>" in own

    # A Lead looking at someone else's comment gets Delete alone.
    lead_view = render_to_string(
        "comments/comment_item.html",
        {"comment": comment, "task": task, "is_project_manager": True},
    )
    assert "<svg" not in lead_view
    assert ">Delete</button>" in lead_view
    assert "Edit" not in lead_view


@pytest.mark.django_db
def test_the_comment_button_is_not_coral(commentable):
    """Coral is the page's one primary CTA; a comment box is not it."""
    from django.template.loader import render_to_string

    from apps.comments.forms import CommentForm

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")

    posting = render_to_string(
        "comments/comment_form.html", {"task": task, "form": CommentForm()}
    )
    editing = render_to_string(
        "comments/comment_edit_form.html",
        {"comment": comment, "form": CommentForm(instance=comment)},
    )
    for name, html in (("Comment", posting), ("Save", editing)):
        assert "brand-coral" not in html, f"the {name} button is still coral"
        assert "btn-primary" in html, f"the {name} button lost its confirm style"


@pytest.mark.django_db
def test_comment_timestamp_is_small_twelve_hour_and_convertible(commentable):
    """The date reads as a note, in 12-hour form, ready for local restatement."""
    from django.template.loader import render_to_string

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")
    comment.refresh_from_db()

    html = render_to_string(
        "comments/comment_item.html", {"comment": comment, "task": task}
    )
    time_el = html[html.index("<time") : html.index("</time>")]

    # Same size as the dates in the completion matrix.
    assert "text-xs" in time_el and "text-sm" not in time_el
    # AM/PM, not 24-hour. (The visible text only — the datetime attribute
    # beside it is the machine-readable ISO instant.)
    visible = time_el[time_el.rindex(">") + 1 :].strip()
    assert visible == comment.created_at.strftime("%b %-d, %Y %-I:%M %p")
    # The instant is there twice: for machines, and for the browser to convert.
    assert f'data-epoch="{int(comment.created_at.timestamp())}"' in time_el
    assert 'datetime="' in time_el
    assert "window.localTime" in time_el


@pytest.mark.django_db
def test_the_local_time_helper_is_loaded_for_every_page(client, user):
    """The partial calls window.localTime, so the shell has to define it."""
    client.force_login(user)
    page = client.get(reverse("projects:list")).content.decode()
    assert "window.localTime = function" in page


@pytest.mark.django_db
def test_the_comment_body_sits_close_under_its_name(commentable):
    """The body's top margin is the half step, not the full one."""
    from django.template.loader import render_to_string

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")

    html = render_to_string(
        "comments/comment_item.html", {"comment": comment, "task": task}
    )
    body = html[html.rindex('<div class="text-sm text-brand-text') :]
    assert "mt-0.5" in body[: body.index(">")]


@pytest.mark.django_db
def test_the_comment_body_carries_no_whitespace_of_its_own(commentable):
    """The text sits flush in its box, so pre-line adds no phantom lines.

    whitespace-pre-line renders newlines, which is what carries the author's
    own line breaks — and what turned the template's indentation into a blank
    line above the text and another below it.
    """
    from django.template.loader import render_to_string

    _project, task, member = commentable
    comment = Comment.objects.create(
        task=task, author=member, content="first line\nsecond line"
    )

    html = render_to_string(
        "comments/comment_item.html", {"comment": comment, "task": task}
    )
    body = html[html.rindex('<div class="text-sm text-brand-text') :]
    rendered = body[body.index(">") + 1 : body.index("</div>")]
    assert rendered == "first line\nsecond line"  # no leading or trailing break


@pytest.mark.django_db
def test_the_avatar_is_small_and_the_same_in_both_states(commentable):
    """A 24px initial, and the edit form matches so the row does not jump."""
    from django.template.loader import render_to_string

    from apps.comments.forms import CommentForm

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")

    avatar = "w-6 h-6 rounded-full bg-brand-navy"
    row = render_to_string(
        "comments/comment_item.html", {"comment": comment, "task": task}
    )
    editing = render_to_string(
        "comments/comment_edit_form.html",
        {"comment": comment, "form": CommentForm(instance=comment)},
    )
    for name, html in (("row", row), ("edit form", editing)):
        assert avatar in html, f"the {name}'s avatar is not the small one"
        assert "w-8 h-8" not in html, f"the {name} kept the old size"


@pytest.mark.django_db
def test_the_edit_forms_actions_sit_right_with_cancel_first(commentable):
    """Save lands where the Comment button does, with Cancel to its left."""
    from django.template.loader import render_to_string

    from apps.comments.forms import CommentForm

    _project, task, member = commentable
    comment = Comment.objects.create(task=task, author=member, content="x")

    html = render_to_string(
        "comments/comment_edit_form.html",
        {"comment": comment, "form": CommentForm(instance=comment)},
    )
    actions = html[html.index('<div class="flex gap-2') :]
    assert "justify-end" in actions[: actions.index(">")]
    assert actions.index("Cancel") < actions.index("Save")
