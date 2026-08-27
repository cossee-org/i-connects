"""Tests for the contributions ledger.

Covers the closure wiring in ``tasks.services.close_task`` — who earns
credit and who does not — the withdrawal on reopening, and the matrix and
CSV helpers behind the contributions pages.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.contributions.models import ContributionRecord
from apps.contributions.services import (
    get_project_contributions_matrix,
    get_user_contributions,
)
from apps.core.enums import ContributionType, ProjectStatus
from apps.projects.models import ContributionTracking, ProjectMember
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task
from apps.tasks.services import (
    approve_completion,
    clear_review,
    close_task,
    complete_task,
    mark_own_done,
    mark_reviewed,
    reopen_own,
    reopen_task,
)


def _make_project(owner, *, name="P", tracking=ContributionTracking.CREDIT):
    project = create_project_with_owner(
        user=owner,
        form_data={
            "name": name,
            "status": ProjectStatus.ACTIVE,
            "is_public": True,
            "contribution_tracking": tracking,
        },
    )
    col = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    member = ProjectMember.objects.filter(project=project, users=owner).first()
    return project, col, member


def _make_task(project, col, pm_member, *, assignees=None, reviewers=None,
               ctype=ContributionType.OTHER):
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col,
        container_task_order=0,
        name="T",
        contribution_type=ctype,
    )
    for member in assignees or []:
        task.assigned_members.add(member)
    for member in reviewers or []:
        task.reviewers.add(member)
    return task


def _member_for(project, user):
    member = ProjectMember.objects.create(project=project)
    member.users.add(user)
    return member


# ---------------------------------------------------------------------------
# Closing a task is the ledger event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_closing_credits_an_assignee_who_marked_their_part_done(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(
        project, col, pm_member,
        assignees=[pm_member],
        ctype=ContributionType.DATA_CURATION,
    )
    mark_own_done(task=task, member=pm_member)

    assert ContributionRecord.objects.count() == 0
    close_task(task=task, closed_by=pm_member)

    record = ContributionRecord.objects.get(user=user)
    assert record.task_id == task.id
    assert record.project_id == project.id
    assert record.contribution_type == ContributionType.DATA_CURATION
    assert record.verified_by_id == user.id
    assert record.closed_at is not None


@pytest.mark.django_db
def test_closing_skips_an_assignee_who_never_marked_done(user, other_user):
    project, col, pm_member = _make_project(user)
    slacker = _member_for(project, other_user)
    task = _make_task(project, col, pm_member, assignees=[pm_member, slacker])
    mark_own_done(task=task, member=pm_member)

    close_task(task=task, closed_by=pm_member)

    assert set(ContributionRecord.objects.values_list("user_id", flat=True)) == {
        user.id
    }


@pytest.mark.django_db
def test_a_reviewed_task_credits_only_reviewed_submissions(user, other_user, pm_user):
    """With reviewers on the task, a done mark alone is not enough."""
    project, col, lead = _make_project(user)
    alice = _member_for(project, other_user)
    bob = _member_for(project, pm_user)
    task = _make_task(
        project, col, lead, assignees=[alice, bob], reviewers=[lead]
    )
    alice_log = mark_own_done(task=task, member=alice)
    mark_own_done(task=task, member=bob)
    mark_reviewed(task_log=alice_log, reviewer=lead)

    close_task(task=task, closed_by=lead)

    assert set(ContributionRecord.objects.values_list("user_id", flat=True)) == {
        other_user.id
    }


@pytest.mark.django_db
def test_a_task_without_reviewers_needs_no_review(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = mark_own_done(task=task, member=pm_member)
    assert not log.is_reviewed

    close_task(task=task, closed_by=pm_member)
    assert ContributionRecord.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_closing_credits_every_user_behind_a_team_assignee(user, other_user):
    project, col, pm_member = _make_project(user)
    team = ProjectMember.objects.create(project=project, name="Team A")
    team.users.add(user, other_user)
    task = _make_task(project, col, pm_member, assignees=[team])
    mark_own_done(task=task, member=team)

    close_task(task=task, closed_by=pm_member)

    assert set(ContributionRecord.objects.values_list("user_id", flat=True)) == {
        user.id,
        other_user.id,
    }


@pytest.mark.django_db
def test_closing_twice_writes_one_record(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    mark_own_done(task=task, member=pm_member)

    close_task(task=task, closed_by=pm_member)
    close_task(task=task, closed_by=pm_member)
    close_task(task=task, closed_by=pm_member)

    assert ContributionRecord.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_a_project_not_tracking_credit_keeps_no_ledger(user):
    project, col, pm_member = _make_project(user, tracking=ContributionTracking.NONE)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    mark_own_done(task=task, member=pm_member)

    close_task(task=task, closed_by=pm_member)
    assert ContributionRecord.objects.count() == 0


@pytest.mark.django_db
def test_the_last_review_closes_the_task_and_credits_it(user, other_user):
    """mark_reviewed closes a fully-reviewed task — the credit follows."""
    project, col, lead = _make_project(user)
    alice = _member_for(project, other_user)
    task = _make_task(project, col, lead, assignees=[alice], reviewers=[lead])
    log = mark_own_done(task=task, member=alice)

    mark_reviewed(task_log=log, reviewer=lead)

    assert ContributionRecord.objects.filter(user=other_user).exists()


# ---------------------------------------------------------------------------
# Reopening withdraws it again
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reopening_withdraws_the_credit(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    mark_own_done(task=task, member=pm_member)
    close_task(task=task, closed_by=pm_member)
    assert ContributionRecord.objects.count() == 1

    reopen_task(task=task)
    assert ContributionRecord.objects.count() == 0

    # Closing again writes it back.
    close_task(task=task, closed_by=pm_member)
    assert ContributionRecord.objects.count() == 1


@pytest.mark.django_db
def test_undoing_a_done_mark_withdraws_the_credit(user, other_user):
    """reopen_own reopens a task whose completion rested on its reviews."""
    project, col, lead = _make_project(user)
    alice = _member_for(project, other_user)
    task = _make_task(project, col, lead, assignees=[alice], reviewers=[lead])
    log = mark_own_done(task=task, member=alice)
    mark_reviewed(task_log=log, reviewer=lead)
    assert ContributionRecord.objects.count() == 1

    reopen_own(task=task, member=alice)
    assert ContributionRecord.objects.count() == 0


@pytest.mark.django_db
def test_withdrawing_a_review_withdraws_the_credit(user, other_user):
    project, col, lead = _make_project(user)
    alice = _member_for(project, other_user)
    task = _make_task(project, col, lead, assignees=[alice], reviewers=[lead])
    log = mark_own_done(task=task, member=alice)
    mark_reviewed(task_log=log, reviewer=lead)
    assert ContributionRecord.objects.count() == 1

    clear_review(task_log=log, cleared_by=lead)
    assert ContributionRecord.objects.count() == 0


# ---------------------------------------------------------------------------
# Approving a submission is not a ledger event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_approving_a_submission_writes_no_contribution(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    ContributionRecord.objects.all().delete()

    approve_completion(task_log=log, approved=True, verifier_user=user)
    assert ContributionRecord.objects.count() == 0


# ---------------------------------------------------------------------------
# Deleting the work deletes the credit recorded against it
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_deleting_a_task_takes_its_contributions_with_it(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    mark_own_done(task=task, member=pm_member)
    close_task(task=task, closed_by=pm_member)
    assert ContributionRecord.objects.count() == 1

    task.delete()
    assert ContributionRecord.objects.count() == 0


@pytest.mark.django_db
def test_deleting_a_project_takes_its_contributions_with_it(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    mark_own_done(task=task, member=pm_member)
    close_task(task=task, closed_by=pm_member)

    project.delete()
    assert ContributionRecord.objects.count() == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credit(project, col, pm_member, member, ctype):
    """Close a one-assignee task so ``member`` earns one ``ctype`` record."""
    task = _make_task(project, col, pm_member, assignees=[member], ctype=ctype)
    mark_own_done(task=task, member=member)
    close_task(task=task, closed_by=pm_member)
    return task


@pytest.mark.django_db
def test_project_contributions_matrix(user, other_user):
    project, col, pm_member = _make_project(user)
    other_member = _member_for(project, other_user)

    for _ in range(2):
        _credit(project, col, pm_member, pm_member, ContributionType.WRITING_ORIGINAL_DRAFT)
    _credit(project, col, pm_member, other_member, ContributionType.DATA_CURATION)

    matrix = get_project_contributions_matrix(project)
    assert matrix["totals"]["grand_total"] == 3
    assert matrix["totals"][ContributionType.WRITING_ORIGINAL_DRAFT] == 2
    assert matrix["totals"][ContributionType.DATA_CURATION] == 1

    rows_by_user = {row["user"].id: row for row in matrix["rows"]}
    assert rows_by_user[user.id]["counts"][ContributionType.WRITING_ORIGINAL_DRAFT] == 2
    assert rows_by_user[user.id]["total"] == 2
    assert rows_by_user[other_user.id]["counts"][ContributionType.DATA_CURATION] == 1
    # Highest-total user is sorted first.
    assert matrix["rows"][0]["user"].id == user.id


@pytest.mark.django_db
def test_get_user_contributions_scoped_to_project(user):
    project_a, col_a, pm_a = _make_project(user, name="A")
    project_b, col_b, pm_b = _make_project(user, name="B")
    for project, col, pm in ((project_a, col_a, pm_a), (project_b, col_b, pm_b)):
        _credit(project, col, pm, pm, ContributionType.SOFTWARE)

    assert get_user_contributions(user).count() == 2
    assert get_user_contributions(user, project=project_a).count() == 1


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_my_contributions_csv_returns_rows(client, user):
    project, col, pm_member = _make_project(user)
    _credit(project, col, pm_member, pm_member, ContributionType.WRITING_ORIGINAL_DRAFT)

    client.force_login(user)
    resp = client.get("/my/contributions.csv")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")
    assert "attachment;" in resp["Content-Disposition"]
    body = b"".join(resp.streaming_content).decode("utf-8")
    lines = [line for line in body.splitlines() if line]
    # header + one data row
    assert lines[0].split(",")[0] == "project"
    assert len(lines) == 2
    assert user.email in lines[1]
    assert "Writing" in lines[1]


@pytest.mark.django_db
def test_project_contributions_csv_requires_pm(client, user, other_user):
    """Ordinary members get the matrix page but NOT the raw CSV."""
    project, col, pm_member = _make_project(user)
    _member_for(project, other_user)
    _credit(project, col, pm_member, pm_member, ContributionType.SOFTWARE)

    # PM can download.
    client.force_login(user)
    resp = client.get(f"/projects/{project.pk}/contributions.csv")
    assert resp.status_code == 200

    # Non-PM cannot.
    client.force_login(other_user)
    resp = client.get(f"/projects/{project.pk}/contributions.csv")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_my_contributions_csv_empty_only_header(client, user):
    client.force_login(user)
    resp = client.get("/my/contributions.csv")
    assert resp.status_code == 200
    body = b"".join(resp.streaming_content).decode("utf-8")
    lines = [line for line in body.splitlines() if line]
    assert len(lines) == 1  # header only


# ---------------------------------------------------------------------------
# The project's Contribution Tracking tab
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tracking_tab_shows_the_matrix(client, user, other_user):
    project, col, pm = _make_project(user)
    other = _member_for(project, other_user)
    _credit(project, col, pm, pm, ContributionType.SOFTWARE)
    _credit(project, col, pm, other, ContributionType.DATA_CURATION)

    client.force_login(user)
    body = client.get(
        reverse("projects:contribution_tracking", kwargs={"pk": project.pk})
    ).content.decode()
    assert "Contributions by member" in body
    assert "Software" in body
    assert "Data curation" in body
    assert "Alice" in body
    assert "Bob" in body


@pytest.mark.django_db
def test_tracking_tab_lists_a_member_with_no_credit_yet(client, user, other_user):
    """A gap in the credit should be as visible as the credit itself."""
    project, col, pm = _make_project(user)
    _member_for(project, other_user)
    _credit(project, col, pm, pm, ContributionType.SOFTWARE)

    client.force_login(user)
    body = client.get(
        reverse("projects:contribution_tracking", kwargs={"pk": project.pk})
    ).content.decode()
    assert "Bob" in body


@pytest.mark.django_db
def test_tracking_tab_explains_an_untracked_project(client, user):
    project, _col, _pm = _make_project(user, tracking=ContributionTracking.NONE)
    client.force_login(user)
    body = client.get(
        reverse("projects:contribution_tracking", kwargs={"pk": project.pk})
    ).content.decode()
    assert "tracking contributions" in body
    assert "Contributions by member" not in body


@pytest.mark.django_db
def test_the_old_project_contributions_url_redirects_to_the_tab(client, user):
    project, _col, _pm = _make_project(user)
    client.force_login(user)
    resp = client.get(f"/projects/{project.pk}/contributions/")
    assert resp.status_code == 302
    assert resp["Location"] == reverse(
        "projects:contribution_tracking", kwargs={"pk": project.pk}
    )
