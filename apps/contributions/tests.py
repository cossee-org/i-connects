"""Tests for the contributions ledger.

Covers the append-only invariants (save/delete guards), the task-approval
wiring in ``tasks.services.approve_completion``, and the matrix helper used
by the per-project contributions page.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from apps.contributions.models import ContributionRecord
from apps.contributions.services import (
    get_project_contributions_matrix,
    get_user_contributions,
    record_contributions_for_approval,
)
from apps.core.enums import ContributionType, ProjectStatus
from apps.projects.models import ProjectMember
from apps.projects.services import create_project_with_owner
from apps.tasks.models import KanbanBoard, Task
from apps.tasks.services import approve_completion, complete_task


def _make_project(owner, *, name="P"):
    project = create_project_with_owner(
        user=owner,
        form_data={"name": name, "status": ProjectStatus.ACTIVE, "is_public": True},
    )
    col = KanbanBoard.objects.create(project=project, name="Todo", container_order=0)
    member = ProjectMember.objects.filter(project=project, users=owner).first()
    return project, col, member


def _make_task(project, col, pm_member, *, assignees=None, ctype=ContributionType.OTHER):
    task = Task.objects.create(
        project=project,
        created_by=pm_member,
        container=col,
        container_task_order=0,
        name="T",
        contribution_type=ctype,
    )
    if assignees:
        for m in assignees:
            task.assigned_members.add(m)
    return task


# ---------------------------------------------------------------------------
# Immutability guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_save_on_existing_record_raises(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=True, verifier_user=user)

    rec = ContributionRecord.objects.get(task_log=log, user=user)
    rec.contribution_type = ContributionType.WRITING
    with pytest.raises(IntegrityError):
        rec.save()


@pytest.mark.django_db
def test_delete_raises(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=True, verifier_user=user)

    rec = ContributionRecord.objects.get(task_log=log, user=user)
    with pytest.raises(IntegrityError):
        rec.delete()


# ---------------------------------------------------------------------------
# Approval wiring
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_approval_creates_contribution_per_user(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(
        project, col, pm_member,
        assignees=[pm_member],
        ctype=ContributionType.DATA_CURATION,
    )
    log = complete_task(task=task, completed_by=pm_member)

    assert ContributionRecord.objects.count() == 0
    approve_completion(task_log=log, approved=True, verifier_user=user)
    rec = ContributionRecord.objects.get(task_log=log, user=user)
    assert rec.project_id == project.id
    assert rec.task_id == task.id
    assert rec.contribution_type == ContributionType.DATA_CURATION
    assert rec.verified_by_id == user.id
    assert rec.closed_at is not None


@pytest.mark.django_db
def test_approval_is_idempotent(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)

    approve_completion(task_log=log, approved=True, verifier_user=user)
    approve_completion(task_log=log, approved=True, verifier_user=user)
    approve_completion(task_log=log, approved=True, verifier_user=user)
    assert ContributionRecord.objects.filter(task_log=log, user=user).count() == 1


@pytest.mark.django_db
def test_rejection_creates_no_contribution(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=False, verifier_user=user)
    assert ContributionRecord.objects.count() == 0


@pytest.mark.django_db
def test_null_approval_creates_no_contribution(user):
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=None, verifier_user=user)
    assert ContributionRecord.objects.count() == 0


@pytest.mark.django_db
def test_approval_after_rejection_still_creates_record(user):
    """Toggling rejection then approval should still produce exactly one record."""
    project, col, pm_member = _make_project(user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=False, verifier_user=user)
    approve_completion(task_log=log, approved=True, verifier_user=user)
    assert ContributionRecord.objects.filter(task_log=log, user=user).count() == 1


@pytest.mark.django_db
def test_task_with_team_member_creates_record_per_user(user, other_user):
    project, col, pm_member = _make_project(user)
    # Team-shaped ProjectMember holding both users.
    team_member = ProjectMember.objects.create(project=project, name="Team A")
    team_member.users.add(user, other_user)
    task = _make_task(project, col, pm_member, assignees=[team_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=True, verifier_user=user)
    records = ContributionRecord.objects.filter(task_log=log)
    assert records.count() == 2
    assert set(records.values_list("user_id", flat=True)) == {user.id, other_user.id}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_project_contributions_matrix(user, other_user):
    project, col, pm_member = _make_project(user)
    # Give other_user their own membership so both users are project members.
    other_member = ProjectMember.objects.create(project=project)
    other_member.users.add(other_user)

    # user gets two WRITING contributions; other_user gets one DATA_CURATION.
    for ctype in (ContributionType.WRITING, ContributionType.WRITING):
        task = _make_task(project, col, pm_member, assignees=[pm_member], ctype=ctype)
        log = complete_task(task=task, completed_by=pm_member)
        approve_completion(task_log=log, approved=True, verifier_user=user)

    task2 = _make_task(
        project, col, pm_member,
        assignees=[other_member],
        ctype=ContributionType.DATA_CURATION,
    )
    log2 = complete_task(task=task2, completed_by=pm_member)
    approve_completion(task_log=log2, approved=True, verifier_user=user)

    matrix = get_project_contributions_matrix(project)
    assert matrix["totals"]["grand_total"] == 3
    assert matrix["totals"][ContributionType.WRITING] == 2
    assert matrix["totals"][ContributionType.DATA_CURATION] == 1

    rows_by_user = {row["user"].id: row for row in matrix["rows"]}
    assert rows_by_user[user.id]["counts"][ContributionType.WRITING] == 2
    assert rows_by_user[user.id]["total"] == 2
    assert rows_by_user[other_user.id]["counts"][ContributionType.DATA_CURATION] == 1
    # Highest-total user is sorted first.
    assert matrix["rows"][0]["user"].id == user.id


@pytest.mark.django_db
def test_get_user_contributions_scoped_to_project(user):
    project_a, col_a, pm_a = _make_project(user, name="A")
    project_b, col_b, pm_b = _make_project(user, name="B")
    for project, col, pm in ((project_a, col_a, pm_a), (project_b, col_b, pm_b)):
        task = _make_task(project, col, pm, assignees=[pm])
        log = complete_task(task=task, completed_by=pm)
        approve_completion(task_log=log, approved=True, verifier_user=user)

    assert get_user_contributions(user).count() == 2
    assert get_user_contributions(user, project=project_a).count() == 1


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_my_contributions_csv_returns_rows(client, user):
    project, col, pm_member = _make_project(user)
    task = _make_task(
        project, col, pm_member,
        assignees=[pm_member],
        ctype=ContributionType.WRITING,
    )
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=True, verifier_user=user)

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
    # Add other_user as a non-PM member.
    from apps.projects.models import ProjectMember
    member = ProjectMember.objects.create(project=project)
    member.users.add(other_user)
    task = _make_task(project, col, pm_member, assignees=[pm_member])
    log = complete_task(task=task, completed_by=pm_member)
    approve_completion(task_log=log, approved=True, verifier_user=user)

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
