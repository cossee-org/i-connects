"""Service helpers for the contributions ledger.

Closing a task is the ledger event: :func:`record_contributions_for_task`
writes the credit, :func:`withdraw_contributions_for_task` takes it back when
the task reopens. The read side (``get_user_contributions``,
``get_project_contributions``, and the matrix builders) is kept separate so
the ledger could later be extracted or replaced.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.core.enums import ContributionType, TaskStatus
from apps.projects.models import Project, ProjectMember
from apps.tasks.models import Task, TaskLog

from .models import ContributionRecord

User = get_user_model()


def eligible_contributions(task: Task) -> list[tuple[ProjectMember, TaskLog]]:
    """``(assignee, task_log)`` for everyone who has earned credit on ``task``.

    An assignee qualifies once their own current TaskLog says COMPLETED —
    the mark is theirs to make, so nobody else's click stands in for it. A
    task that carries reviewers asks one thing more: that submission has to
    have been reviewed, since there the reviewers' sign-off is what says the
    work landed.

    Read off the task's *live* assignee and reviewer sets, so a member taken
    off the task stops qualifying and one added brings an unmarked row with
    them.
    """
    current: dict[int, TaskLog] = {}
    for log in task.task_logs.all():  # newest-first; first seen is current
        current.setdefault(log.assigned_to_id, log)

    needs_review = task.reviewers.exists()
    eligible: list[tuple[ProjectMember, TaskLog]] = []
    for member in task.assigned_members.all():
        log = current.get(member.pk)
        if log is None or log.status != TaskStatus.COMPLETED:
            continue
        if needs_review and not log.is_reviewed:
            continue
        eligible.append((member, log))
    return eligible


@transaction.atomic
def record_contributions_for_task(
    task: Task,
    *,
    verifier_user: Optional[User] = None,
) -> list[ContributionRecord]:
    """Write the credit a closed task earned, one row per contributing user.

    Called from ``tasks.services.close_task``. Only projects tracking
    contributions as CRediT keep a ledger, so this is a no-op elsewhere.

    Idempotent: the ``(task_log, user)`` unique constraint plus
    ``get_or_create`` means re-closing a task writes no second row.

    ``verifier_user`` is the lead who closed the task, stored as
    ``verified_by``. It may be ``None`` when closure came from a code path
    with no user behind it (a data migration, an admin action) — the FK is
    nullable for that reason.
    """
    if not task.project.tracks_credit:
        return []

    closed_at = timezone.now()
    created: list[ContributionRecord] = []
    for member, task_log in eligible_contributions(task):
        for user in member.users.all():
            obj, was_created = ContributionRecord.objects.get_or_create(
                task_log=task_log,
                user=user,
                defaults=dict(
                    task=task,
                    project=task.project,
                    contribution_type=task.contribution_type,
                    verified_by=verifier_user,
                    closed_at=closed_at,
                ),
            )
            if was_created:
                created.append(obj)
    return created


@transaction.atomic
def withdraw_contributions_for_task(task: Task) -> int:
    """Take back the credit a task's closure wrote, and say how many rows went.

    The mirror of :func:`record_contributions_for_task`, called when a task
    reopens. Credit is a claim that a piece of work is finished; a reopened
    task is not, so the claim goes with it rather than standing over work
    that has been pulled back.
    """
    deleted, _by_model = ContributionRecord.objects.filter(task=task).delete()
    return deleted


def get_user_contributions(
    user: User,
    *,
    project: Optional[Project] = None,
):
    """Return a user's contributions, optionally scoped to one project."""
    qs = ContributionRecord.objects.filter(user=user).select_related(
        "task", "project", "verified_by"
    )
    if project is not None:
        qs = qs.filter(project=project)
    return qs


def get_project_contributions(project: Project):
    """All contributions for a project, newest first."""
    return (
        ContributionRecord.objects.filter(project=project)
        .select_related("task", "user", "verified_by")
    )


def get_user_contribution_counts_by_type(user: User) -> dict[str, int]:
    """Total contributions by type for the given user (across all projects)."""
    counts: dict[str, int] = defaultdict(int)
    for ctype in (
        ContributionRecord.objects.filter(user=user)
        .values_list("contribution_type", flat=True)
    ):
        counts[ctype] += 1
    # Return a plain dict keyed by the enum value.
    return dict(counts)


def get_project_contributions_matrix(project: Project) -> dict:
    """Build a members × contribution-type matrix for a project.

    Returns a dict with:
        - ``types``: list of (value, label) for every ContributionType
        - ``rows``:  list of ``{"user": User, "counts": {type: int}, "total": int}``
                     one entry per user who has at least one contribution in
                     the project, sorted by total desc then by name.
        - ``totals``: dict ``{type: int}`` of column totals, plus
                     ``"grand_total"`` for the full count. Underscore-free
                     keys so Django templates can reach them.

    Read-only; computed from the append-only table.
    """
    type_choices = list(ContributionType.choices)  # [(value, label), ...]
    type_values = [v for v, _ in type_choices]

    qs = (
        ContributionRecord.objects.filter(project=project)
        .select_related("user")
        .only("user_id", "contribution_type", "user__name", "user__email")
    )

    users_by_id: dict[int, User] = {}
    per_user: dict[int, dict[str, int]] = defaultdict(lambda: {v: 0 for v in type_values})
    column_totals: dict[str, int] = {v: 0 for v in type_values}
    grand_total = 0

    for rec in qs:
        users_by_id[rec.user_id] = rec.user
        per_user[rec.user_id][rec.contribution_type] += 1
        column_totals[rec.contribution_type] += 1
        grand_total += 1

    rows = []
    for uid, counts in per_user.items():
        rows.append(
            {
                "user": users_by_id[uid],
                "counts": counts,
                "total": sum(counts.values()),
            }
        )
    rows.sort(key=lambda r: (-r["total"], (r["user"].name or r["user"].email or "").lower()))

    column_totals["grand_total"] = grand_total
    return {"types": type_choices, "rows": rows, "totals": column_totals}
