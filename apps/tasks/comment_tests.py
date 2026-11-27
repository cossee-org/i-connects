"""Comments left with a review or a verification.

Ticking Reviewed or Verified opens the comments dialog
(task_comments_modal.html), which posts the verdict with optional comments.
View comments shows them to the assignee, the task's reviewers and Leads,
and offers Edit to whoever may change them. A comment goes with its
verdict.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from apps.audit.models import AuditAction, AuditLog
from apps.notifications.models import Notification
from apps.projects.models import ProjectMember
from apps.tasks.deadline_tests import _setup
from apps.tasks.models import TaskLog
from apps.tasks.services import close_task, mark_own_done, mark_reviewed, verify_part


def _block(client, task):
    return client.get(
        reverse("tasks:status_block", kwargs={"pk": task.pk}) + "?pane=1"
    ).content.decode()


def _rows(html, member):
    start = html.index(f'data-assignee-person="{member.pk}"')
    return html[start : html.index("</tbody>", start)]


def _json_before(html, marker):
    i = html.index(marker)
    script = html.rindex("<script", 0, i)
    return json.loads(re.search(r">(.*?)</script>", html[script:], re.S).group(1))


def _review(client, task, log, **data):
    return client.post(
        reverse("tasks:review_log", kwargs={"pk": task.pk, "log_pk": log.pk}),
        {"pane_mode": "1", "reviewed": "1", **data}, HTTP_HX_REQUEST="true",
    )


def _verify(client, task, log, **data):
    return client.post(
        reverse("tasks:verify_log", kwargs={"pk": task.pk, "log_pk": log.pk}),
        {"pane_mode": "1", "verified": "1", **data}, HTTP_HX_REQUEST="true",
    )


def _edit(client, task, log, **data):
    return client.post(
        reverse("tasks:log_comment", kwargs={"pk": task.pk, "log_pk": log.pk}),
        {"pane_mode": "1", **data}, HTTP_HX_REQUEST="true",
    )


@pytest.fixture
def done(pm_user, user, other_user, django_user_model):
    """Bob's part is done; Carol reviews the task; Eve is a plain member."""
    from django.utils import timezone

    from apps.core.enums import MemberPrivileges
    from apps.projects.models import ProjectPrivilege

    project, task, lead, [bob, carol] = _setup(pm_user, user, other_user, deadline_days=10)
    task.reviewers.add(carol)
    eve_user = django_user_model.objects.create_user(
        email="eve@test.com", password="password123", name="Eve",
        email_confirmed_at=timezone.now(),
    )
    eve = ProjectMember.objects.create(project=project)
    eve.users.add(eve_user)
    ProjectPrivilege.objects.create(project=project, user=eve_user, privilege=MemberPrivileges.CONTRIBUTOR)
    log = mark_own_done(task=task, member=bob)
    return task, lead, bob, carol, eve_user, log


@pytest.mark.django_db
def test_ticking_reviewed_or_verified_opens_the_comments_dialog(client, pm_user, done):
    task, _lead, bob, _carol, _eve, _log = done
    client.force_login(pm_user)
    html = _block(client, task)

    rows = _rows(html, bob)
    assert 'data-open-comments="review"' in rows and 'data-open-comments="verify"' in rows
    review = _json_before(rows, 'data-open-comments="review"')
    assert review["mode"] == "mark" and review["confirm"] == "Mark reviewed"
    assert review["title"] == f"Mark {bob.display_name}'s part reviewed"
    verify = _json_before(rows, 'data-open-comments="verify"')
    assert verify["title"] == f"Verify {bob.display_name}'s contribution"
    assert "data-comments-modal" in html


@pytest.mark.django_db
def test_a_review_carries_its_comments(client, other_user, done):
    task, _lead, bob, carol, _eve, log = done
    client.force_login(other_user)

    response = _review(client, task, log, comment="  Checked the figures.  ")

    assert response.status_code == 200
    log.refresh_from_db()
    assert log.reviewed_by == carol
    assert log.review_comment == "Checked the figures."
    assert log.review_comment_updated_at is not None
    row = AuditLog.objects.filter(action=AuditAction.TASK_REVIEWED).latest("id")
    assert row.metadata["comment"] == "Checked the figures."
    note = Notification.objects.for_user(bob.users.first()).latest("id")
    assert "left comments: Checked the figures." in note.message
    # View comments sits under Reviewed, for the reviewer.
    rows = _rows(response.content.decode(), bob)
    view = _json_before(rows, 'data-view-comments="review"')
    assert view["comment"] == "Checked the figures." and view["canEdit"] is True
    assert view["author"] == carol.display_name


@pytest.mark.django_db
def test_a_verdict_without_comments_shows_no_link(client, pm_user, done):
    task, _lead, bob, _carol, _eve, log = done
    client.force_login(pm_user)

    response = _verify(client, task, log)

    log.refresh_from_db()
    assert log.verified_by is not None and log.verify_comment == ""
    assert "data-view-comments" not in _rows(response.content.decode(), bob)


@pytest.mark.django_db
def test_only_the_assignee_reviewers_and_leads_see_comments(client, pm_user, user, other_user, done):
    task, lead, bob, carol, eve_user, log = done
    mark_reviewed(task_log=log, reviewer=carol, comment="Needs a source.")
    verify_part(task_log=log, lead=lead, comment="Credited.")

    for viewer in (pm_user, user, other_user):
        client.force_login(viewer)
        rows = _rows(_block(client, task), bob)
        assert 'data-view-comments="review"' in rows
        assert 'data-view-comments="verify"' in rows

    client.force_login(eve_user)
    html = _block(client, task)
    assert "Needs a source." not in html and "Credited." not in html
    assert "data-view-comments" not in html


@pytest.mark.django_db
def test_who_may_edit_comments(client, pm_user, user, other_user, done):
    task, lead, _bob, carol, _eve, log = done
    mark_reviewed(task_log=log, reviewer=carol, comment="First look.")
    verify_part(task_log=log, lead=lead, comment="Ok.")

    # The assignee may read but not change either.
    client.force_login(user)
    assert _edit(client, task, log, kind="review", comment="x").status_code == 403
    assert _edit(client, task, log, kind="verify", comment="x").status_code == 403
    # The reviewer changes their own review's comments, not the Lead's.
    client.force_login(other_user)
    assert _edit(client, task, log, kind="review", comment="Second look.").status_code == 200
    assert _edit(client, task, log, kind="verify", comment="x").status_code == 403
    # A Lead may change both.
    client.force_login(pm_user)
    assert _edit(client, task, log, kind="verify", comment="").status_code == 200

    log.refresh_from_db()
    assert (log.review_comment, log.verify_comment) == ("Second look.", "")
    assert log.verify_comment_updated_at is None
    assert AuditLog.objects.filter(action=AuditAction.TASK_REVIEW_COMMENT_SET).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.TASK_VERIFY_COMMENT_SET).count() == 1
    assert _edit(client, task, log, kind="other", comment="x").status_code == 400


@pytest.mark.django_db
def test_comments_go_with_their_verdict(client, pm_user, user, done):
    task, lead, bob, carol, _eve, log = done
    mark_reviewed(task_log=log, reviewer=carol, comment="Fine.")
    verify_part(task_log=log, lead=lead, comment="Credited.")
    client.force_login(pm_user)

    client.post(
        reverse("tasks:verify_log", kwargs={"pk": task.pk, "log_pk": log.pk}),
        {"verified": "0", "pane_mode": "1"}, HTTP_HX_REQUEST="true",
    )
    log.refresh_from_db()
    assert log.verify_comment == "" and log.review_comment == "Fine."

    client.post(
        reverse("tasks:review_log", kwargs={"pk": task.pk, "log_pk": log.pk}),
        {"reviewed": "0", "pane_mode": "1"}, HTTP_HX_REQUEST="true",
    )
    log.refresh_from_db()
    assert log.review_comment == ""

    # Undoing the done mark takes both.
    mark_reviewed(task_log=log, reviewer=carol, comment="Again.")
    verify_part(task_log=TaskLog.objects.get(pk=log.pk), lead=lead, comment="Again.")
    client.force_login(user)
    client.post(reverse("tasks:undo", kwargs={"pk": task.pk}), {"pane_mode": "1"}, HTTP_HX_REQUEST="true")
    log.refresh_from_db()
    assert (log.review_comment, log.verify_comment) == ("", "")


@pytest.mark.django_db
def test_the_withdraw_confirmation_mentions_comments(client, pm_user, done):
    task, lead, bob, carol, _eve, log = done
    mark_reviewed(task_log=log, reviewer=carol, comment="Fine.")
    verify_part(task_log=log, lead=lead, comment="Credited.")
    client.force_login(pm_user)
    rows = _rows(_block(client, task), bob)

    done_prompt = _json_before(rows, 'data-confirm-withdraw="done"')
    assert done_prompt["lines"][0].endswith("is cleared, along with its comments.")
    assert "along with its comments" in done_prompt["lines"][1]
    assert "The review's comments go with it." in _json_before(rows, 'data-confirm-withdraw="review"')["lines"]
    assert "The verification's comments go with it." in _json_before(rows, 'data-confirm-withdraw="verify"')["lines"]


@pytest.mark.django_db
def test_a_locked_reviews_comments_stay_as_they_are(client, other_user, done):
    task, lead, _bob, carol, _eve, log = done
    mark_reviewed(task_log=log, reviewer=carol, comment="Before closing.")
    close_task(task=task, closed_by=lead)
    client.force_login(other_user)

    assert _edit(client, task, log, kind="review", comment="After.").status_code == 403


@pytest.mark.django_db
def test_overlong_comments_are_refused(client, pm_user, done):
    task, _lead, _bob, _carol, _eve, log = done
    client.force_login(pm_user)

    response = _verify(client, task, log, comment="x" * 2001)

    assert response.status_code == 400
    log.refresh_from_db()
    assert log.verified_by is None
