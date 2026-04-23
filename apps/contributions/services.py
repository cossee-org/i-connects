"""Service helpers for the contributions ledger.

The public surface per Ultra Plan §6 "Contribution ledger ... with a clean API
(recordContribution, getUserContributions, getProjectContributions) so it can
later be extracted or replaced".
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.core.enums import ContributionType
from apps.projects.models import Project
from apps.tasks.models import TaskLog

from .models import ContributionRecord

User = get_user_model()


@transaction.atomic
def record_contributions_for_approval(
    task_log: TaskLog,
    *,
    verifier_user: Optional[User] = None,
) -> list[ContributionRecord]:
    """Create one ContributionRecord per user behind ``task_log.assigned_to``.

    Called from ``tasks.services.approve_completion`` when ``approved is True``.
    Idempotent: the ``(task_log, user)`` unique constraint plus ``get_or_create``
    guarantees that re-approving the same log writes no new rows.

    ``verifier_user`` is stored on the record as ``verified_by``. It may be
    ``None`` if closure came from a non-user code path (admin action, data
    migration) — the FK is nullable for that reason.
    """

    task = task_log.task
    users = list(task_log.assigned_to.users.all())
    if not users:
        return []

    closed_at = timezone.now()
    created: list[ContributionRecord] = []
    for user in users:
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
